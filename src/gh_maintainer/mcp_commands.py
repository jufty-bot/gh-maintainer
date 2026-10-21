"""CLI query, analysis, policy, and context contracts exposed as MCP tools."""

from pathlib import Path
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ConfigDict, Field

from gh_maintainer.clock import parse_date
from gh_maintainer.config import (
    Config,
    ListOptions,
    PolicyFile,
    RepositoryPolicy,
    SelectedPolicy,
    check_policy,
    load_policy,
)
from gh_maintainer.context import ContextRequest, context_for
from gh_maintainer.database import Database
from gh_maintainer.mcp_contracts import configured_tool
from gh_maintainer.queries import CatalogQueries
from gh_maintainer.repositories import AnalysisService
from gh_maintainer.schemas import (
    AnalysisInput,
    AnalysisView,
    ChangePage,
    CommentPage,
    Contract,
    ItemDetail,
    ItemFilters,
    ItemPage,
    Kind,
    Overview,
    ProcessingView,
    PullCommitPage,
    PullFiles,
    repository_name,
)
from gh_maintainer.skillpack import install_skills, skill_directory

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
LOCAL_WRITE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, open_world_hint=False
)


class SkillResult(Contract):
    skills: list[str]
    installed_to: str | None


class ListRequest(ListOptions):
    """CLI list options plus the current author/date/triage filters."""

    model_config = ConfigDict(extra="forbid", strict=False)


class StatusRequest(ListRequest):
    state: Literal["open", "closed", "merged", "all"] = "all"


async def guard_policy(
    db: Database,
    item_id: str,
    path: str | None,
    action: str,
    *,
    labels: tuple[str, ...] = (),
    score: float | None = None,
    disposition: str | None = None,
) -> None:
    if path is not None:
        async with db.sessions() as session:
            item = (await CatalogQueries(session).show(item_id)).item
        selected = load_policy(Path(path), item.repo)
        check_policy(
            selected.policy, action, labels=labels, score=score, disposition=disposition
        )


def register_commands(server: MCPServer[None], db: Database, config: Config) -> None:
    tool = configured_tool(server, config)

    async def page(kind: Kind, options: ListRequest) -> ItemPage:
        if options.unprocessed and options.processing:
            raise ValueError("unprocessed and processing cannot be combined.")
        values = options.model_dump(exclude={"format", "search", "unprocessed"})
        values.update(
            kind=kind,
            q=options.search,
            repo=repository_name(options.repo) if options.repo else "",
            state=None if options.state == "all" else options.state,
            processing="pending" if options.unprocessed else options.processing,
        )
        async with db.sessions() as session:
            return await CatalogQueries(session).page(
                ItemFilters.model_validate(values)
            )

    @tool(annotations=READ_ONLY, nested=("options", ListRequest))
    async def prs(options: ListRequest | None = None) -> ItemPage:
        """List PRs (open by default) with CLI filters and pagination."""
        return await page(Kind.PR, options or ListRequest())

    @tool(annotations=READ_ONLY, nested=("options", ListRequest))
    async def issues(options: ListRequest | None = None) -> ItemPage:
        """List issues (open by default) with CLI filtering and pagination options."""
        return await page(Kind.ISSUE, options or ListRequest())

    @tool(annotations=READ_ONLY, nested=("options", StatusRequest))
    async def status(options: StatusRequest | None = None) -> ItemPage:
        """List PRs in all states by default, including signals and processing."""
        return await page(Kind.PR, options or StatusRequest())

    @tool(annotations=READ_ONLY)
    async def show(item_id: str) -> ItemDetail:
        """Read an item by issue node ID or OWNER/REPO#NUMBER, including revision."""
        async with db.sessions() as session:
            return await CatalogQueries(session).show(item_id)

    @tool(annotations=READ_ONLY)
    async def repos() -> Overview:
        """Read repository inventory, activity snapshots, counts and sync coverage."""
        async with db.sessions() as session:
            return await CatalogQueries(session).overview()

    @tool(annotations=READ_ONLY)
    async def files(item_id: str) -> PullFiles:
        """Read Files Changed: the full indexed PR git diff, head/base and freshness.

        Accepts OWNER/REPO#NUMBER or issue node ID. Read diff for the unified patch;
        diff=null means unavailable. Inspect stale and coverage_error before review.
        Sync discovers new commits; this read uses the same snapshot as the dashboard.
        """
        async with db.sessions() as session:
            return await CatalogQueries(session).files(item_id)

    @tool(annotations=READ_ONLY)
    async def commits(
        item_id: str,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> PullCommitPage:
        """Read Commits: the current ordered PR commit list with SQL pagination.

        Accepts OWNER/REPO#NUMBER or issue node ID. Includes full messages, authors,
        dates and GitHub links. files contains head/base/fetch time and freshness;
        use the files tool for the PR diff. Sync refreshes this offline snapshot.
        """
        async with db.sessions() as session:
            return await CatalogQueries(session).commits(item_id, limit, offset)

    @tool(annotations=READ_ONLY)
    async def comments(
        item_id: str,
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
        include_unavailable: bool = False,
    ) -> CommentPage:
        """Read paginated indexed comments, inline replies and review bodies."""
        async with db.sessions() as session:
            return await CatalogQueries(session).comments(
                item_id, limit, offset, include_unavailable
            )

    @tool(annotations=READ_ONLY)
    async def changes(
        item_id: str | None = None,
        since: str | None = None,
        all: bool = False,  # noqa: A002
        limit: Annotated[int, Field(ge=1, le=500)] = 100,
        offset: Annotated[int, Field(ge=0)] = 0,
    ) -> ChangePage:
        """Read changes since processing by default; since and all are exclusive."""
        if since is not None and all:
            raise ValueError("since and all cannot be combined.")
        async with db.sessions() as session:
            return await CatalogQueries(session).changes(
                item_id,
                parse_date(since) if since else None,
                not all and not since,
                limit,
                offset,
            )

    async def save(
        item_id: str,
        kind: str,
        data: AnalysisInput,
        revision: int | None,
        policy: str | None,
        action: str,
    ) -> AnalysisView:
        await guard_policy(
            db,
            item_id,
            policy,
            action,
            labels=tuple(data.labels or ()),
            score=data.score,
        )
        return await AnalysisService(db).save(item_id, kind, data, revision)

    @tool(annotations=LOCAL_WRITE)
    async def classify(
        item_id: str,
        labels: Annotated[list[str], Field(min_length=1)],
        producer: str = "manual",
        reason: str = "",
        revision: Annotated[int, Field(ge=1)] | None = None,
        policy: str | None = None,
    ) -> AnalysisView:
        """Replace local labels, with optional revision and policy guards."""
        return await save(
            item_id,
            "classification",
            AnalysisInput(labels=labels, producer=producer, reason=reason),
            revision,
            policy,
            "classify",
        )

    @tool(annotations=LOCAL_WRITE)
    async def summarize(
        item_id: str,
        text: str,
        producer: str = "manual",
        reason: str = "",
        revision: Annotated[int, Field(ge=1)] | None = None,
        policy: str | None = None,
    ) -> AnalysisView:
        """Save caller-supplied summary text. Text is literal, including '-'."""
        return await save(
            item_id,
            "summary",
            AnalysisInput(text=text, producer=producer, reason=reason),
            revision,
            policy,
            "summarize",
        )

    @tool(annotations=LOCAL_WRITE)
    async def rank(
        item_id: str,
        score: Annotated[float, Field(allow_inf_nan=False)],
        producer: str = "manual",
        reason: str = "",
        revision: Annotated[int, Field(ge=1)] | None = None,
        policy: str | None = None,
    ) -> AnalysisView:
        """Save a supplied finite rank score; higher ranks sort first."""
        return await save(
            item_id,
            "ranking",
            AnalysisInput(score=score, producer=producer, reason=reason),
            revision,
            policy,
            "rank",
        )

    @tool(annotations=LOCAL_WRITE)
    async def mark_processed(
        item_id: str,
        revision: Annotated[int, Field(ge=1)] | None = None,
        actor: str = "manual",
        policy: str | None = None,
    ) -> ProcessingView:
        """Acknowledge a revision; requires complete item and comment coverage."""
        await guard_policy(db, item_id, policy, "mark-processed")
        return await AnalysisService(db).mark_processed(item_id, revision, actor)

    @tool(
        annotations=ToolAnnotations(read_only_hint=True, open_world_hint=True),
        nested=("request", ContextRequest),
    )
    async def context(
        item_id: str, request: ContextRequest | None = None
    ) -> CallToolResult:
        """Fetch live commit-bound guidance, code, CI or review evidence through gh.

        Incomplete evidence sets isError=true and retains the structured packet.
        """
        async with db.sessions() as session:
            item = (await CatalogQueries(session).show(item_id)).item
        packet = await context_for(item, request or ContextRequest())
        return CallToolResult(
            content=[TextContent(type="text", text=packet.model_dump_json(indent=2))],
            structured_content=packet.model_dump(mode="json"),
            is_error=not packet.complete,
        )

    @tool(annotations=LOCAL_WRITE)
    async def policy(
        repo: str, file: str = ".gh-maintainer.json", initialize: bool = False
    ) -> SelectedPolicy:
        """Read repository policy; initialize creates a file without overwriting."""
        name = repository_name(repo)
        path = Path(file)
        if initialize:
            content = PolicyFile(repositories={name: RepositoryPolicy()})
            with path.open("x", encoding="utf-8") as stream:
                stream.write(content.model_dump_json(indent=2) + "\n")
        return load_policy(path, name)

    @tool(annotations=LOCAL_WRITE)
    async def skills(install: str | None = None) -> SkillResult:
        """List bundled skills or install to a server directory without overwriting."""
        names = (
            install_skills(Path(install))
            if install
            else sorted(
                path.name
                for path in skill_directory().iterdir()
                if (path / "SKILL.md").is_file()
            )
        )
        return SkillResult(
            skills=names, installed_to=str(Path(install).resolve()) if install else None
        )

    @tool(annotations=READ_ONLY)
    async def db_path() -> str:
        """Return the index path. Database deletion requires stopping MCP."""
        path = str(db.engine.url.database)
        if path == ":memory:":
            raise ValueError("An in-memory database has no file to manage.")
        return str(Path(path).resolve())
