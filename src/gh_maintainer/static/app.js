const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const results = $("#results");
const limit = 25;
const dispositions = {
    inbox: "Inbox",
    planned: "Planned",
    waiting: "Waiting",
    snoozed: "Snoozed",
    done: "Done",
};
const views = {
    queue: [
        "Maintenance queue",
        "Your next moves",
        "See what needs your attention. Keep your projects moving.",
    ],
    triage: [
        "Triage inbox",
        "A place for every next step",
        "Review the context. Make a decision. Leave a clear next action.",
    ],
    repos: [
        "Repositories",
        "The whole portfolio",
        "A little perspective on everything you maintain.",
    ],
    pr: [
        "Pull requests",
        "Changes in motion",
        "Keep reviews, checks, and contributions moving forward.",
    ],
    issue: [
        "Issues",
        "Work to understand",
        "Turn incoming questions and bugs into clear next steps.",
    ],
    all: [
        "All indexed items",
        "The complete local index",
        "Explore your portfolio, including closed and merged work.",
    ],
};
let view = "queue",
    offset = 0,
    total = 0,
    overview,
    generation = 0;
let focus = "",
    disposition = "inbox",
    pageItems = [],
    detailId = null,
    detailGeneration = 0;
let dirty = false,
    saving = false,
    searchTimer,
    toastTimer;

function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
}
function button(text, className, action) {
    const node = el("button", text, className);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
}
function safeLink(url, text) {
    const link = el("a", text);
    try {
        const parsed = new URL(url);
        if (parsed.protocol === "https:" && parsed.hostname === "github.com")
            link.href = parsed.href;
    } catch {}
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
}
function pullIcon() {
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [name, value] of Object.entries({
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        "stroke-width": "1.6",
        "aria-hidden": "true",
    }))
        svg.setAttribute(name, value);
    for (const [tag, attributes] of [
        ["circle", { cx: 6, cy: 5, r: 2.5 }],
        ["circle", { cx: 6, cy: 19, r: 2.5 }],
        ["circle", { cx: 18, cy: 19, r: 2.5 }],
        ["path", { d: "M6 7.5v9M18 16.5V9a4 4 0 0 0-4-4h-2m2-2-2 2 2 2" }],
    ]) {
        const child = document.createElementNS(
            "http://www.w3.org/2000/svg",
            tag,
        );
        for (const [name, value] of Object.entries(attributes))
            child.setAttribute(name, value);
        svg.append(child);
    }
    return svg;
}
function formatDate(value) {
    return value ? new Date(value).toLocaleString() : "Not observed";
}
function badge(text, type = "") {
    return el("span", text, `badge ${type}`);
}
function notify(message) {
    clearTimeout(toastTimer);
    $("#toast").textContent = message;
    $("#toast").hidden = false;
    toastTimer = setTimeout(() => {
        $("#toast").hidden = true;
    }, 4000);
}
async function api(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
        let message = `Local API returned ${response.status}. Please try again.`;
        try {
            const body = await response.json();
            if (typeof body.detail === "string") message = body.detail;
            else if (Array.isArray(body.detail))
                message = body.detail.map((error) => error.msg).join(". ");
        } catch {}
        throw new Error(message);
    }
    return response.json();
}
function emptyState(title, message, retry = false) {
    const node = el("div", undefined, "empty");
    node.append(
        el("span", retry ? "↻" : "✓", "empty-icon"),
        el("h3", title),
        el("p", message),
    );
    node.append(
        button(
            retry ? "Try again" : "Reset filters",
            "button secondary",
            typeof retry === "function"
                ? retry
                : retry
                  ? refresh
                  : resetFilters,
        ),
    );
    return node;
}
async function loadOverview() {
    overview = await api("/api/overview");
    $("#owner").textContent =
        overview.owners && overview.owners.length > 1
            ? `${overview.owners.length} owners / portfolio`
            : overview.owner
              ? `@${overview.owner} / portfolio`
              : "Your workspace";
    for (const [key, value] of Object.entries(overview.counts)) {
        const target = $(`#metric-${key}`);
        if (target) target.textContent = value.toLocaleString();
    }
    const run = overview.latest_run;
    const incomplete = overview.counts.incomplete_repositories;
    const warning = !!incomplete || !!(run && run.status !== "complete");
    $("#sync-status").classList.toggle("warn", warning);
    $("#sync-status").textContent = run
        ? `${warning ? "◐" : "●"} Last sync ${formatDate(run.finished_at || run.started_at)} · ${run.status}${incomplete ? ` · ${incomplete} repositories incomplete` : ""} · Refresh reads the local index`
        : "No sync yet. Run gh-maintainer sync, or seed a demo index to explore.";
    const selected = $("#repo").value;
    $("#repo").replaceChildren(
        new Option("All repositories", ""),
        ...overview.repos.map((repo) => new Option(repo.name, repo.name)),
    );
    $("#repo").value = selected;
    $("#generated").textContent =
        `Index read at ${new Date(overview.generated_at).toLocaleTimeString()}`;
    // This count spans every repository, independently of the current list filters.
    try {
        const inbox = await api(
            "/api/items?state=open&disposition=inbox&limit=1",
        );
        $("#inbox-count").textContent = inbox.total || "";
    } catch {
        $("#inbox-count").textContent = "";
    }
}
function renderItem(item) {
    const row = el("article", undefined, "item-row");
    const icon = el(
        "span",
        item.kind === "pr" ? "" : "◎",
        `item-icon ${item.kind}`,
    );
    icon.title = item.kind === "pr" ? "Pull request" : "Issue";
    if (item.kind === "pr") icon.append(pullIcon());
    const center = el("div", undefined, "item-center");
    const title = button(undefined, "item-title", () => showDetail(item.id));
    title.append(
        markdown(item.title, item.url, { inline: true, links: false }),
    );
    center.append(title);
    const meta = el("div", undefined, "item-meta");
    meta.append(safeLink(item.url, `${item.repo} #${item.number}`));
    if (item.author) meta.append(el("span", `by ${item.author}`));
    meta.append(badge(item.draft ? "Draft" : item.state, item.state));
    for (const label of item.labels.slice(0, 2))
        meta.append(badge(label, "label"));
    center.append(meta);
    if (item.note) center.append(el("p", item.note, "item-note"));
    const side = el("div", undefined, "item-side");
    if (item.reasons.length)
        side.append(
            el(
                "span",
                item.reasons[0],
                `reason ${item.score >= 60 ? "warning" : ""}`,
            ),
        );
    else side.append(badge(dispositions[item.disposition], item.disposition));
    const bottom = el("div", undefined, "side-meta");
    if (item.reasons.length)
        bottom.append(badge(dispositions[item.disposition], item.disposition));
    bottom.append(
        el(
            "span",
            item.age_days ? `Updated ${item.age_days}d ago` : "Updated today",
            "timestamp",
        ),
    );
    side.append(bottom);
    row.append(icon, center, side);
    return row;
}
function renderRepos() {
    const search = $("#search").value.toLowerCase();
    const repos = overview.repos.filter(
        (repo) =>
            `${repo.name} ${repo.raw.description || ""} ${repo.raw.language || ""}`
                .toLowerCase()
                .includes(search) &&
            (!$("#repo").value || repo.name === $("#repo").value),
    );
    results.replaceChildren(
        ...repos.map((repo) => {
            const card = el("article", undefined, "repo-card");
            const top = el("div", undefined, "repo-card-heading"),
                title = el("h3");
            title.append(safeLink(repo.url, repo.name));
            top.append(
                title,
                badge(
                    repo.archived
                        ? "Archived"
                        : repo.raw.private
                          ? "Private"
                          : "Public",
                ),
            );
            card.append(
                top,
                markdown(
                    repo.raw.description || "No description provided.",
                    repo.url,
                ),
            );
            const meta = el("div", undefined, "repo-stats");
            meta.append(
                safeLink(
                    `${repo.url}/stargazers`,
                    `${repo.raw.stargazers_count?.toLocaleString() ?? "—"} stars`,
                ),
                safeLink(
                    `${repo.url}/forks`,
                    `${repo.raw.forks_count?.toLocaleString() ?? "—"} forks`,
                ),
                el("span", `${repo.open_prs} open PRs`),
                el("span", `${repo.open_issues} open issues`),
                el("span", repo.raw.language || "No language"),
            );
            card.append(meta);
            const bottom = el("div", undefined, "repo-card-bottom");
            bottom.append(
                el(
                    "span",
                    repo.error
                        ? "◐ Incomplete sync"
                        : !repo.present
                          ? "No longer visible"
                          : repo.synced_at
                            ? "● Indexed"
                            : "○ Not indexed",
                    repo.error ? "amber" : "muted",
                ),
            );
            if (repo.error) bottom.title = repo.error;
            bottom.append(
                button(
                    `${repo.attention} need attention →`,
                    "text-button",
                    () => {
                        setView("queue");
                        $("#repo").value = repo.name;
                        loadItems();
                    },
                ),
            );
            card.append(bottom);
            if (repo.synced_at)
                card.append(
                    el(
                        "small",
                        `Items synced ${formatDate(repo.synced_at)}`,
                        "timestamp",
                    ),
                );
            return card;
        }),
    );
    if (!repos.length)
        results.append(
            emptyState(
                "No repositories found",
                "Try another search or sync your repositories.",
            ),
        );
    results.classList.add("repo-grid");
    $("#total").textContent = repos.length;
    $(".pagination").hidden = true;
}
function updateFilterControls() {
    for (const node of $$("[data-focus]")) {
        const active = node.dataset.focus === focus;
        node.classList.toggle("active", active);
        node.setAttribute("aria-pressed", active);
    }
    for (const node of $$("[data-disposition]")) {
        const active = node.dataset.disposition === disposition;
        node.classList.toggle("active", active);
        node.setAttribute("aria-pressed", active);
    }
    $("#clear-filters").hidden = !(
        $("#search").value ||
        $("#repo").value ||
        focus ||
        $("#state").value !== (view === "all" ? "" : "open") ||
        $("#sort").value !== "priority" ||
        (view === "triage" && disposition !== "inbox")
    );
}
async function loadItems() {
    const currentGeneration = ++generation;
    updateFilterControls();
    results.classList.remove("repo-grid");
    if (view === "repos" && overview) {
        renderRepos();
        results.setAttribute("aria-busy", "false");
        return;
    }
    $(".pagination").hidden = false;
    results.setAttribute("aria-busy", "true");
    $("#previous").disabled = true;
    $("#next").disabled = true;
    results.replaceChildren(
        ...Array.from({ length: 4 }, () => {
            const row = el("div", undefined, "skeleton-row");
            row.append(el("div"), el("div"));
            row.setAttribute("aria-hidden", "true");
            return row;
        }),
    );
    const query = new URLSearchParams({
        q: $("#search").value,
        repo: $("#repo").value,
        state: $("#state").value,
        sort: $("#sort").value,
        focus,
        limit,
        offset,
    });
    if (view === "queue") {
        query.set("queue", "true");
        query.set("state", "open");
    }
    if (view === "triage") {
        query.set("state", "open");
        query.set("disposition", disposition);
    }
    if (view === "pr" || view === "issue") query.set("kind", view);
    try {
        const data = await api(`/api/items?${query}`);
        if (currentGeneration !== generation) return;
        // A local decision or a sync can remove the last item on a later page.
        if (offset && offset >= data.total) {
            offset = Math.max(0, Math.ceil(data.total / limit) * limit - limit);
            return loadItems();
        }
        pageItems = data.items;
        total = data.total;
        $("#total").textContent = total.toLocaleString();
        results.replaceChildren(...pageItems.map(renderItem));
        if (!pageItems.length)
            results.append(
                emptyState(
                    view === "triage"
                        ? "This lane is clear"
                        : "You’re all caught up here",
                    "Try a different filter or refresh after your next sync.",
                ),
            );
        $("#previous").disabled = offset === 0;
        $("#next").disabled = offset + limit >= total;
        $("#page").textContent = total
            ? `Showing ${offset + 1}–${Math.min(offset + limit, total)} of ${total}`
            : "0 items";
    } catch (error) {
        if (currentGeneration !== generation) return;
        pageItems = [];
        $("#total").textContent = "—";
        $("#page").textContent = "Could not load items";
        results.replaceChildren(
            emptyState("Couldn’t load your index", error.message, true),
        );
    } finally {
        if (currentGeneration === generation)
            results.setAttribute("aria-busy", "false");
    }
}
function markdown(body, sourceUrl, { inline = false, links = true } = {}) {
    const container = el(
        inline ? "span" : "div",
        undefined,
        inline ? "markdown markdown-inline" : "markdown",
    );
    if (!body) {
        container.append(el("p", "No description provided.", "muted"));
        return container;
    }
    if (!globalThis.marked || !globalThis.DOMPurify) {
        container.append(el("pre", body));
        return container;
    }
    const fragment = DOMPurify.sanitize(
        inline
            ? marked.parseInline(body, { gfm: true })
            : marked.parse(body, { gfm: true, breaks: true }),
        {
            USE_PROFILES: { html: true },
            RETURN_DOM_FRAGMENT: true,
            FORBID_TAGS: [
                "style",
                "form",
                "button",
                "iframe",
                "video",
                "audio",
            ],
            FORBID_ATTR: ["style", "id", "name"],
        },
    );
    for (const link of fragment.querySelectorAll("a")) {
        if (!links) {
            link.replaceWith(...link.childNodes);
            continue;
        }
        if (!link.hasAttribute("href")) continue;
        try {
            const url = new URL(link.getAttribute("href"), sourceUrl);
            if (!["https:", "http:", "mailto:"].includes(url.protocol))
                link.removeAttribute("href");
            else link.href = url.href;
        } catch {
            link.removeAttribute("href");
        }
        link.target = "_blank";
        link.rel = "noopener noreferrer";
    }
    for (const image of fragment.querySelectorAll("img")) {
        const src = image.getAttribute("src");
        try {
            const url = new URL(src, sourceUrl);
            if (!src || url.protocol !== "https:") {
                image.remove();
                continue;
            }
            image.src = url.href;
        } catch {
            image.remove();
            continue;
        }
        image.loading = "lazy";
        image.referrerPolicy = "no-referrer";
        image.removeAttribute("srcset");
    }
    for (const input of fragment.querySelectorAll("input"))
        input.disabled = true;
    container.append(fragment);
    for (const pre of container.querySelectorAll("pre")) {
        const code = pre.textContent;
        pre.append(
            button("Copy", "copy-code", async (event) => {
                const target = event.currentTarget;
                try {
                    await navigator.clipboard.writeText(code);
                    target.textContent = "Copied";
                } catch {
                    notify(
                        "Could not copy. Select the code to copy it manually.",
                    );
                }
            }),
        );
    }
    return container;
}
function renderDiscussion(item) {
    const section = el("section", undefined, "discussion");
    section.append(
        el("h3", item.kind === "pr" ? "Comments and reviews" : "Comments"),
    );
    const status = el("p", "Loading comments…", "help-text");
    status.setAttribute("role", "status");
    const entries = el("div", undefined, "comment-list");
    const controls = el("div");
    section.append(status, entries, controls);
    let offset = 0;
    async function load() {
        controls.replaceChildren();
        status.textContent = "Loading comments…";
        try {
            const data = await api(
                `/api/items/${encodeURIComponent(item.id)}/comments?limit=50&offset=${offset}`,
            );
            if (!section.isConnected) return;
            status.textContent = data.coverage_error
                ? `Incomplete conversation coverage: ${data.coverage_error}`
                : !data.comments_synced_at
                  ? "Comments have not been synced yet. Run gh-maintainer sync --full to index this discussion."
                  : `${data.total.toLocaleString()} comments and reviews · Synced ${formatDate(data.comments_synced_at)}`;
            status.classList.toggle("signal-warning", !!data.coverage_error);
            for (const comment of data.comments) {
                const card = el("article", undefined, "comment");
                const meta = el("div", undefined, "comment-meta");
                meta.append(
                    el(
                        "strong",
                        comment.author ? `@${comment.author}` : "Deleted user",
                    ),
                    el(
                        "span",
                        {
                            issue: "Comment",
                            inline: "Inline comment",
                            review: "Review",
                        }[comment.kind],
                    ),
                );
                if (comment.raw.state)
                    meta.append(
                        el(
                            "span",
                            comment.raw.state
                                .toLowerCase()
                                .replaceAll("_", " "),
                        ),
                    );
                if (comment.created_at)
                    meta.append(el("time", formatDate(comment.created_at)));
                meta.append(safeLink(comment.url, "View on GitHub ↗"));
                card.append(meta);
                if (comment.raw.path) {
                    const line = comment.raw.line ?? comment.raw.original_line;
                    card.append(
                        el(
                            "code",
                            `${comment.raw.path}${line ? `:${line}` : ""}`,
                            "comment-path",
                        ),
                    );
                }
                if (comment.raw.diff_hunk) {
                    const context = el("details");
                    const pre = el("pre");
                    pre.append(el("code", comment.raw.diff_hunk));
                    context.append(el("summary", "Code context"), pre);
                    card.append(context);
                }
                card.append(
                    markdown(comment.body || "No comment body.", comment.url),
                );
                entries.append(card);
            }
            offset += data.comments.length;
            if (!data.total && data.comments_synced_at)
                entries.append(
                    el("p", "No comments or reviews yet.", "help-text"),
                );
            if (offset < data.total)
                controls.append(
                    button("Load more comments", "button secondary", load),
                );
        } catch (error) {
            if (!section.isConnected) return;
            status.textContent = `Could not load comments: ${error.message}`;
            controls.append(button("Retry comments", "button secondary", load));
        }
    }
    // Start after renderDetail has attached this section to the current dialog.
    queueMicrotask(load);
    return section;
}
function canLeaveDetail() {
    return (
        !saving &&
        (!dirty || window.confirm("Discard your unsaved triage changes?"))
    );
}
function updateDetailNavigation() {
    const index = pageItems.findIndex((item) => item.id === detailId);
    $("#previous-item").disabled = saving || index <= 0;
    $("#next-item").disabled =
        saving || index < 0 || index >= pageItems.length - 1;
}
function renderDetail({ item, changes }) {
    const content = $("#detail-content");
    dirty = false;
    const heading = el("div", undefined, "detail-heading");
    heading.append(el("div", `${item.repo} / #${item.number}`, "eyebrow"));
    const title = el("h2");
    title.append(markdown(item.title, item.url, { inline: true }));
    title.id = "detail-title";
    heading.append(title);
    const meta = el("div", undefined, "detail-meta");
    meta.append(
        badge(item.draft ? "Draft" : item.state, item.state),
        el(
            "span",
            `${item.kind === "pr" ? "Pull request" : "Issue"} by ${item.author}`,
        ),
        el("span", `Updated ${formatDate(item.updated_at)}`),
        safeLink(item.url, "Open on GitHub ↗"),
    );
    heading.append(meta);
    const layout = el("div", undefined, "detail-layout"),
        main = el("div", undefined, "detail-main"),
        side = el("div", undefined, "triage-panel");
    if (item.repo_error)
        main.append(
            el(
                "p",
                `Incomplete synchronization: ${item.repo_error}`,
                "signal-warning",
            ),
        );
    if (!item.available || !item.present)
        main.append(
            el(
                "p",
                "Retained history: this item or its repository is no longer visible in the latest inventory.",
                "signal-warning",
            ),
        );
    if (item.reasons.length) {
        const reasons = el("div", undefined, "detail-reasons");
        for (const reason of item.reasons)
            reasons.append(badge(reason, item.score >= 60 ? "warning" : ""));
        main.append(reasons);
    }
    const description = el("section", undefined, "description");
    const tabs = el("div", undefined, "description-tabs");
    const body = el("div");
    const preview = button("Description", "active", () => {
        preview.classList.add("active");
        source.classList.remove("active");
        preview.setAttribute("aria-pressed", "true");
        source.setAttribute("aria-pressed", "false");
        body.replaceChildren(markdown(item.raw.body, item.url));
    });
    const source = button("Markdown source", "", () => {
        source.classList.add("active");
        preview.classList.remove("active");
        source.setAttribute("aria-pressed", "true");
        preview.setAttribute("aria-pressed", "false");
        body.replaceChildren(
            el(
                "pre",
                item.raw.body || "No description provided.",
                "markdown-source",
            ),
        );
    });
    preview.setAttribute("aria-pressed", "true");
    source.setAttribute("aria-pressed", "false");
    tabs.append(preview, source);
    body.append(markdown(item.raw.body, item.url));
    description.append(tabs, body);
    main.append(description, renderDiscussion(item));
    const observations = el("details", undefined, "observations");
    observations.append(
        el("summary", `Local observations (${changes.length})`),
    );
    observations.append(
        el(
            "p",
            "Source snapshots observed during sync. This is not a complete GitHub activity feed.",
            "help-text",
        ),
    );
    if (!changes.length)
        observations.append(
            el("p", "No observations recorded yet.", "help-text"),
        );
    for (const change of changes.slice(0, 8)) {
        const entry = el("div", undefined, "observation");
        entry.append(
            el(
                "span",
                change.before_json ? "Source data changed" : "First indexed",
            ),
            el("time", formatDate(change.observed_at)),
        );
        observations.append(entry);
    }
    main.append(observations);
    side.append(
        el("h3", "Local triage"),
        el(
            "p",
            "Choose a next step for this item. Saved to your local workspace.",
            "help-text",
        ),
    );
    const form = el("form", undefined, "triage-form");
    const statusLabel = el("label", "Disposition"),
        status = el("select");
    status.id = "triage-disposition";
    for (const [value, label] of Object.entries(dispositions))
        status.append(new Option(label, value));
    status.value = item.disposition;
    statusLabel.htmlFor = status.id;
    const untilLabel = el("label", "Snooze until"),
        until = el("input");
    until.id = "triage-until";
    until.type = "datetime-local";
    untilLabel.htmlFor = until.id;
    if (item.snoozed_until) {
        const date = new Date(item.snoozed_until);
        until.value = new Date(
            date.getTime() - date.getTimezoneOffset() * 60000,
        )
            .toISOString()
            .slice(0, 16);
    }
    const snooze = el("div", undefined, "snooze-fields");
    snooze.append(
        untilLabel,
        until,
        el("p", "Uses your device’s timezone.", "help-text"),
    );
    function toggleSnooze() {
        snooze.hidden = status.value !== "snoozed";
        until.required = !snooze.hidden;
    }
    status.addEventListener("change", toggleSnooze);
    toggleSnooze();
    const noteLabel = el("label", "Next action / note"),
        note = el("textarea");
    note.id = "triage-note";
    noteLabel.htmlFor = note.id;
    note.value = item.note || "";
    note.rows = 5;
    note.maxLength = 10000;
    note.placeholder = "What should happen next?";
    const notePreview = el("div", undefined, "note-preview");
    function previewNote() {
        notePreview.hidden = !note.value;
        notePreview.replaceChildren(markdown(note.value, item.url));
    }
    note.addEventListener("input", previewNote);
    previewNote();
    const error = el("p", "", "form-error");
    error.setAttribute("role", "alert");
    error.hidden = true;
    const save = el("button", "Save decision", "button primary");
    save.type = "submit";
    const saveNext = el("button", "Save & next →", "button secondary");
    saveNext.type = "submit";
    const currentIndex = pageItems.findIndex((row) => row.id === item.id);
    const nextId = currentIndex >= 0 ? pageItems[currentIndex + 1]?.id : null;
    saveNext.disabled = !nextId;
    form.append(
        statusLabel,
        status,
        snooze,
        noteLabel,
        note,
        notePreview,
        error,
        save,
        saveNext,
    );
    form.addEventListener("input", () => {
        dirty = true;
    });
    form.addEventListener("change", () => {
        dirty = true;
    });
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (saving) return;
        const advance = event.submitter === saveNext;
        const savedGeneration = detailGeneration;
        error.hidden = true;
        let deadline = null;
        if (status.value === "snoozed") {
            const date = new Date(until.value);
            if (
                !until.value ||
                !Number.isFinite(date.getTime()) ||
                date <= new Date()
            ) {
                error.textContent =
                    "Choose a future date and time to snooze this item.";
                error.hidden = false;
                until.focus();
                return;
            }
            deadline = date.toISOString();
        }
        saving = true;
        updateDetailNavigation();
        $("#close-detail").disabled = true;
        for (const control of form.elements) control.disabled = true;
        save.textContent = "Saving…";
        try {
            const detail = await api(
                `/api/items/${encodeURIComponent(item.id)}/triage`,
                {
                    method: "PUT",
                    headers: {
                        "Content-Type": "application/json",
                        "X-Maintainer-Request": "1",
                    },
                    body: JSON.stringify({
                        disposition: status.value,
                        note: note.value,
                        snoozed_until: deadline,
                    }),
                },
            );
            dirty = false;
            saving = false;
            $("#close-detail").disabled = false;
            renderDetail(detail);
            notify("Decision saved to your local workspace.");
            await refresh();
            if (savedGeneration !== detailGeneration || !$("#detail").open)
                return;
            if (dirty || saving) return;
            if (advance && nextId) showDetail(nextId);
            else updateDetailNavigation();
        } catch (failure) {
            error.textContent = failure.message;
            error.hidden = false;
            saving = false;
            $("#close-detail").disabled = false;
            for (const control of form.elements) control.disabled = false;
            saveNext.disabled = !nextId;
            save.textContent = "Save decision";
            updateDetailNavigation();
        }
    });
    side.append(form);
    if (item.kind === "pr") {
        const signals = el("div", undefined, "signals");
        signals.append(el("h4", "Pull request signals"));
        for (const [label, value] of [
            ["Checks", item.checks],
            ["Review", item.review],
            ["Mergeability", item.mergeable],
        ]) {
            const row = el("div");
            row.append(
                el("span", label),
                el("strong", value.toLowerCase().replaceAll("_", " ")),
            );
            signals.append(row);
        }
        signals.append(
            el(
                "p",
                `Observed ${formatDate(item.signals_at)}${item.signals_stale ? " · Signals are stale" : ""}`,
                "help-text",
            ),
        );
        side.append(signals);
    }
    const context = el("div", undefined, "item-context");
    context.append(
        el("h4", "Details"),
        el(
            "p",
            `Assignees: ${item.assignees.join(", ") || "Unassigned"}`,
            "help-text",
        ),
    );
    const labels = el("div", undefined, "detail-reasons");
    for (const label of item.labels) labels.append(badge(label, "label"));
    context.append(labels);
    context.append(
        button("Copy item ID", "text-button", async () => {
            try {
                await navigator.clipboard.writeText(item.id);
                notify("Item ID copied.");
            } catch {
                notify(`Item ID: ${item.id}`);
            }
        }),
    );
    side.append(context);
    layout.append(main, side);
    content.replaceChildren(heading, layout);
    updateDetailNavigation();
}
async function showDetail(id) {
    if ($("#detail").open && !canLeaveDetail()) return;
    const token = ++detailGeneration;
    detailId = id;
    dirty = false;
    const loading = el("h2", "Loading item…", "detail-loading");
    loading.id = "detail-title";
    $("#detail-content").replaceChildren(loading);
    if (!$("#detail").open) $("#detail").showModal();
    $("#detail").scrollTop = 0;
    updateDetailNavigation();
    try {
        const detail = await api(`/api/items/${encodeURIComponent(id)}`);
        if (token !== detailGeneration || !$("#detail").open) return;
        renderDetail(detail);
    } catch (error) {
        if (token === detailGeneration && $("#detail").open) {
            const failure = emptyState(
                "Couldn’t load this item",
                error.message,
                () => showDetail(id),
            );
            failure.querySelector("h3").id = "detail-title";
            $("#detail-content").replaceChildren(failure);
        }
    }
}
function setView(nextView) {
    clearTimeout(searchTimer);
    view = nextView;
    offset = 0;
    focus = "";
    disposition = "inbox";
    $("#search").value = "";
    $("#repo").value = "";
    $("#sort").value = "priority";
    $("#state").value = view === "all" ? "" : "open";
    for (const node of $$(".sidebar [data-view]")) {
        node.classList.toggle("active", node.dataset.view === view);
        if (node.dataset.view === view)
            node.setAttribute("aria-current", "page");
        else node.removeAttribute("aria-current");
    }
    $("#title").textContent = views[view][0];
    $("#list-title").textContent = views[view][1];
    $("#subtitle").textContent = views[view][2];
    document.title = `${views[view][0]} · Maintainer`;
    $("#state").hidden = ["queue", "triage", "repos"].includes(view);
    $("#sort").hidden = view === "repos";
    $("#focus-bar").hidden = view === "repos";
    $("#triage-tabs").hidden = view !== "triage";
    $("#start-triage").hidden = view === "repos";
    $(".list-hint").textContent =
        view === "repos"
            ? "Coverage across your local index"
            : "Open an item to review & triage";
    return loadItems();
}
function resetFilters() {
    clearTimeout(searchTimer);
    $("#search").value = "";
    $("#repo").value = "";
    $("#state").value = view === "all" ? "" : "open";
    $("#sort").value = "priority";
    offset = 0;
    focus = "";
    disposition = "inbox";
    loadItems();
}
async function refresh() {
    $("#refresh").disabled = true;
    try {
        await loadOverview();
        await loadItems();
    } catch (error) {
        $("#sync-status").textContent = `Could not refresh: ${error.message}`;
        $("#sync-status").classList.add("warn");
        if (!overview) {
            results.setAttribute("aria-busy", "false");
            results.replaceChildren(
                emptyState("Couldn’t load your workspace", error.message, true),
            );
        }
    } finally {
        $("#refresh").disabled = false;
    }
}
for (const node of $$("[data-view]"))
    node.addEventListener("click", () => setView(node.dataset.view));
for (const node of $$("[data-focus]"))
    node.addEventListener("click", () => {
        focus = node.dataset.focus;
        offset = 0;
        loadItems();
    });
for (const node of $$("[data-disposition]"))
    node.addEventListener("click", () => {
        disposition = node.dataset.disposition;
        offset = 0;
        loadItems();
    });
$("#attention-metric").addEventListener("click", () => {
    setView("queue");
    focus = "attention";
    loadItems();
});
$("#start-triage").addEventListener("click", async () => {
    if (view !== "triage") await setView("triage");
    if (pageItems.length) showDetail(pageItems[0].id);
    else
        notify(
            "No items in this lane. Choose another lane or adjust your filters.",
        );
});
$("#search").addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
        offset = 0;
        loadItems();
    }, 180);
});
for (const selector of ["#repo", "#state", "#sort"])
    $(selector).addEventListener("change", () => {
        offset = 0;
        loadItems();
    });
$("#clear-filters").addEventListener("click", resetFilters);
$("#previous").addEventListener("click", () => {
    offset = Math.max(0, offset - limit);
    loadItems();
});
$("#next").addEventListener("click", () => {
    offset += limit;
    loadItems();
});
$("#refresh").addEventListener("click", refresh);
function closeDetail() {
    if (canLeaveDetail()) $("#detail").close();
}
$("#close-detail").addEventListener("click", closeDetail);
$("#detail").addEventListener("cancel", (event) => {
    event.preventDefault();
    closeDetail();
});
$("#detail").addEventListener("close", () => {
    ++detailGeneration;
    dirty = false;
    detailId = null;
});
function moveDetail(delta) {
    const index = pageItems.findIndex((item) => item.id === detailId);
    if (index >= 0 && pageItems[index + delta])
        showDetail(pageItems[index + delta].id);
}
$("#previous-item").addEventListener("click", () => moveDetail(-1));
$("#next-item").addEventListener("click", () => moveDetail(1));
document.addEventListener("keydown", (event) => {
    if (
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        event.target.closest(
            'input, textarea, select, [contenteditable="true"]',
        )
    )
        return;
    if (event.key === "/" && !$("#detail").open) {
        event.preventDefault();
        $("#search").focus();
    }
    if ($("#detail").open && ["j", "k"].includes(event.key)) {
        event.preventDefault();
        moveDetail(event.key === "j" ? 1 : -1);
    }
});
window.addEventListener("beforeunload", (event) => {
    if (dirty || saving) {
        event.preventDefault();
        event.returnValue = "";
    }
});
refresh();
