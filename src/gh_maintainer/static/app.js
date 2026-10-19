const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const results = $("#results");
const limit = 25;
let dashboardSync = null;
let syncPoll;
let syncOptions = {};
let syncSubmitting = false;
let credentialAvailable = false;
let activeListQuery = null;
let boardSignature = "";
let boardOffsets = {};
let boardMoving = false;
const boardLimit = 10;
const dispositions = {
    inbox: "Inbox",
    planned: "Planned",
    waiting: "Waiting",
    snoozed: "Snoozed",
    done: "Done",
};
const views = {
    board: [
        "Work board",
        "Work at a glance",
        "Move local decisions forward. Open a card to read the context and finish a review.",
    ],
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
        const error = new Error(message);
        error.status = response.status;
        throw error;
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
        : "No sync yet. Choose Set up repositories to get started.";
    $("#setup-banner").hidden = overview.counts.repositories > 0;
    if (dashboardSync) renderSyncStatus(dashboardSync);
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
    if (item.category)
        meta.append(
            el("span", item.category.replace(/_/g, " "), "category-tag"),
        );
    if (item.priority)
        meta.append(el("span", item.priority, `priority-tag ${item.priority}`));
    if (item.triaged) meta.append(el("span", "Triaged", "triaged-tag"));
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
        $("#state").value !== (["all", "board"].includes(view) ? "" : "open") ||
        $("#sort").value !== "priority" ||
        [
            "#category",
            "#priority",
            "#triaged",
            "#author",
            "#since",
            "#until",
        ].some((selector) => $(selector)?.value) ||
        (view === "triage" && disposition !== "inbox")
    );
}
async function loadItems() {
    const currentGeneration = ++generation;
    updateFilterControls();
    results.classList.remove("repo-grid", "kanban-board");
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
    if ($("#category")?.value) query.set("category", $("#category").value);
    if ($("#priority")?.value) query.set("priority", $("#priority").value);
    if ($("#triaged")?.value) query.set("triaged", $("#triaged").value);
    if ($("#author")?.value) query.set("author", $("#author").value);
    if ($("#since")?.value)
        query.set("since", $("#since").value + "T00:00:00Z");
    if ($("#until")?.value)
        query.set("until", $("#until").value + "T23:59:59Z");
    activeListQuery = new URLSearchParams(query);
    try {
        if (view === "board") {
            await loadBoard(query, currentGeneration);
            return;
        }
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
async function loadBoard(query, token) {
    query.delete("offset");
    query.set("limit", boardLimit);
    const signature = query.toString();
    if (signature !== boardSignature) {
        boardSignature = signature;
        boardOffsets = {};
    }
    async function lanePage(lane) {
        const selection = new URLSearchParams(query);
        selection.set("disposition", lane);
        selection.set("offset", boardOffsets[lane] || 0);
        const data = await api(`/api/items?${selection}`);
        if (token !== generation) return data;
        if (boardOffsets[lane] && boardOffsets[lane] >= data.total) {
            boardOffsets[lane] = Math.max(
                0,
                Math.ceil(data.total / boardLimit) * boardLimit - boardLimit,
            );
            return lanePage(lane);
        }
        return data;
    }
    const lanes = Object.keys(dispositions);
    const pages = await Promise.all(lanes.map(lanePage));
    if (token !== generation) return;
    pageItems = pages.flatMap((data) => data.items);
    total = pages.reduce((sum, data) => sum + data.total, 0);
    $("#total").textContent = total.toLocaleString();
    $(".pagination").hidden = true;
    results.classList.add("kanban-board");
    results.replaceChildren(
        ...lanes.map((lane, index) => {
            const data = pages[index];
            const column = el(
                "section",
                undefined,
                `kanban-column lane-${lane}`,
            );
            column.dataset.lane = lane;
            column.setAttribute("aria-label", `${dispositions[lane]} column`);
            const heading = el("div", undefined, "kanban-heading");
            heading.append(
                el("h3", dispositions[lane]),
                el("span", data.total.toLocaleString(), "count"),
            );
            column.append(heading);
            const cards = el("div", undefined, "kanban-cards");
            cards.append(...data.items.map(renderBoardCard));
            if (!data.items.length)
                cards.append(el("p", "No matching items", "help-text"));
            column.append(cards);
            const start = boardOffsets[lane] || 0;
            const navigation = el("div", undefined, "kanban-pagination");
            const previous = button("←", "button secondary", () => {
                boardOffsets[lane] = Math.max(0, start - boardLimit);
                loadItems();
            });
            previous.setAttribute(
                "aria-label",
                `Previous ${dispositions[lane]} cards`,
            );
            previous.disabled = start === 0;
            const next = button("→", "button secondary", () => {
                boardOffsets[lane] = start + boardLimit;
                loadItems();
            });
            next.setAttribute("aria-label", `Next ${dispositions[lane]} cards`);
            next.disabled = start + data.items.length >= data.total;
            navigation.append(
                previous,
                el(
                    "span",
                    data.total
                        ? `${start + 1}–${start + data.items.length} of ${data.total}`
                        : "0 items",
                ),
                next,
            );
            column.append(navigation);
            column.addEventListener("dragover", (event) => {
                if (
                    boardMoving ||
                    !event.dataTransfer.types.includes("text/plain")
                )
                    return;
                event.preventDefault();
                event.dataTransfer.dropEffect = "move";
            });
            column.addEventListener("drop", (event) => {
                event.preventDefault();
                const item = pageItems.find(
                    (candidate) =>
                        candidate.id ===
                        event.dataTransfer.getData("text/plain"),
                );
                if (item) moveBoardCard(item, lane, column);
            });
            return column;
        }),
    );
}
function renderBoardCard(item) {
    const card = el("article", undefined, "kanban-card");
    card.dataset.itemId = item.id;
    card.draggable = true;
    card.addEventListener("dragstart", (event) => {
        if (boardMoving || event.target.closest("select"))
            return event.preventDefault();
        event.dataTransfer.setData("text/plain", item.id);
        event.dataTransfer.effectAllowed = "move";
    });
    card.append(el("p", `${item.repo} #${item.number}`, "eyebrow"));
    const title = button("", "kanban-title", () => showDetail(item.id));
    title.append(markdown(item.title, item.url, { inline: true }));
    card.append(title);
    const meta = el("div", undefined, "detail-reasons");
    meta.append(
        badge(item.kind === "pr" ? "PR" : "Issue", item.kind),
        badge(item.state, item.state),
    );
    if (item.priority) meta.append(badge(item.priority, "warning"));
    meta.append(badge(item.category.replaceAll("_", " ")));
    card.append(meta);
    if (item.note) card.append(markdown(item.note, item.url));
    card.append(
        el(
            "p",
            item.processing_state === "processed"
                ? "Reviewed"
                : item.processing_state === "changed"
                  ? "Changed since review"
                  : "Needs review",
            "help-text",
        ),
    );
    const move = el("select");
    move.setAttribute("aria-label", `Move ${item.repo}#${item.number} to`);
    for (const [value, label] of Object.entries(dispositions))
        move.append(new Option(label, value));
    move.value = item.disposition;
    move.addEventListener("change", () =>
        moveBoardCard(item, move.value, card),
    );
    card.append(move);
    return card;
}
async function moveBoardCard(item, lane, container) {
    if (boardMoving || lane === item.disposition) return;
    if (lane === "snoozed") {
        const select = container.querySelector("select");
        if (select) select.value = item.disposition;
        await showDetail(item.id);
        if (detailId !== item.id || !$("#triage-disposition")) return;
        $("#triage-disposition").value = lane;
        $("#triage-disposition").dispatchEvent(
            new Event("change", { bubbles: true }),
        );
        $("#triage-until").focus();
        return;
    }
    boardMoving = true;
    const controls = [...results.querySelectorAll("select")];
    controls.forEach((control) => (control.disabled = true));
    container.querySelector(".form-error")?.remove();
    try {
        await api(`/api/items/${encodeURIComponent(item.id)}/triage`, {
            method: "PUT",
            headers: {
                "Content-Type": "application/json",
                "X-Maintainer-Request": "1",
            },
            body: JSON.stringify({
                disposition: lane,
                note: item.note || "",
                priority: item.priority,
                category: item.category,
                revision: item.revision,
            }),
        });
        notify(`Moved to ${dispositions[lane]}. Local decision saved.`);
        await refresh();
    } catch (error) {
        const message = el("p", error.message, "form-error");
        message.setAttribute("role", "alert");
        container.append(message);
        const select = container.querySelector("select");
        if (select) select.value = item.disposition;
    } finally {
        controls.forEach((control) => (control.disabled = false));
        boardMoving = false;
    }
}
function autolinkReferences(container, sourceUrl) {
    if (!sourceUrl) return;
    const repoMatch = sourceUrl.match(
        /https?:\/\/github\.com\/([^/]+)\/([^/#?]+)/,
    );
    const currentRepo = repoMatch ? `${repoMatch[1]}/${repoMatch[2]}` : null;
    const refRegex =
        /(?:([a-zA-Z0-9_.-]+\/[a-zA-Z0-9_.-]+)?#(\d+)|@([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,37}[a-zA-Z0-9])?))/g;

    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
            let parent = node.parentElement;
            while (parent && parent !== container) {
                const tag = parent.tagName.toLowerCase();
                if (["a", "code", "pre", "script", "style"].includes(tag)) {
                    return NodeFilter.FILTER_REJECT;
                }
                parent = parent.parentElement;
            }
            return NodeFilter.FILTER_ACCEPT;
        },
    });

    const textNodes = [];
    while (walker.nextNode()) {
        textNodes.push(walker.currentNode);
    }

    for (const textNode of textNodes) {
        const text = textNode.nodeValue;
        if (!text || (!text.includes("#") && !text.includes("@"))) continue;

        refRegex.lastIndex = 0;
        let lastIndex = 0;
        let match;
        const nodesToInsert = [];

        while ((match = refRegex.exec(text)) !== null) {
            const matchIndex = match.index;
            if (matchIndex > lastIndex) {
                nodesToInsert.push(
                    document.createTextNode(text.slice(lastIndex, matchIndex)),
                );
            }

            const fullMatch = match[0];
            const explicitRepo = match[1];
            const issueNum = match[2];
            const username = match[3];

            if (issueNum) {
                const targetRepo = explicitRepo || currentRepo;
                if (targetRepo) {
                    const link = el("a", fullMatch, "github-reference-link");
                    link.href = `https://github.com/${targetRepo}/issues/${issueNum}`;
                    link.target = "_blank";
                    link.rel = "noopener noreferrer";
                    link.dataset.itemRepo = targetRepo;
                    link.dataset.itemNumber = issueNum;
                    link.addEventListener("click", (event) => {
                        if (
                            event.ctrlKey ||
                            event.metaKey ||
                            event.shiftKey ||
                            event.altKey
                        )
                            return;
                        const found = pageItems.find(
                            (i) =>
                                i.repo.toLowerCase() ===
                                    targetRepo.toLowerCase() &&
                                i.number === Number(issueNum),
                        );
                        if (found) {
                            event.preventDefault();
                            renderDetail({ item: found, changes: [] });
                        }
                    });
                    nodesToInsert.push(link);
                } else {
                    nodesToInsert.push(document.createTextNode(fullMatch));
                }
            } else if (username) {
                const link = el("a", fullMatch, "github-user-mention");
                link.href = `https://github.com/${username}`;
                link.target = "_blank";
                link.rel = "noopener noreferrer";
                nodesToInsert.push(link);
            }

            lastIndex = refRegex.lastIndex;
        }

        if (nodesToInsert.length > 0) {
            if (lastIndex < text.length) {
                nodesToInsert.push(
                    document.createTextNode(text.slice(lastIndex)),
                );
            }
            const parent = textNode.parentNode;
            for (const node of nodesToInsert) {
                parent.insertBefore(node, textNode);
            }
            parent.removeChild(textNode);
        }
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
    if (links) {
        autolinkReferences(fragment, sourceUrl);
    }
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
function renderDiscussion(item, onCoverage = () => {}) {
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
    const threadMap = new Map();
    async function load() {
        onCoverage(false);
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
                const commentId = String(comment.raw.id || comment.id);
                card.dataset.commentId = commentId;

                const inReplyTo =
                    comment.in_reply_to_id ||
                    (comment.raw && comment.raw.in_reply_to_id);
                if (inReplyTo) {
                    card.classList.add("comment-reply");
                }

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
                        }[comment.kind] || "Comment",
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

                if (inReplyTo) {
                    meta.append(
                        el(
                            "span",
                            `↳ Reply to #${inReplyTo}`,
                            "comment-reply-indicator",
                        ),
                    );
                }

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

                if (inReplyTo) {
                    const parentThread =
                        threadMap.get(String(inReplyTo)) ||
                        entries.querySelector(
                            `[data-thread-id="${inReplyTo}"]`,
                        ) ||
                        entries
                            .querySelector(`[data-comment-id="${inReplyTo}"]`)
                            ?.closest(".comment-thread");

                    if (parentThread) {
                        let replies =
                            parentThread.querySelector(".comment-replies");
                        if (!replies) {
                            replies = el("div", undefined, "comment-replies");
                            parentThread.append(replies);
                        }
                        replies.append(card);
                        threadMap.set(commentId, parentThread);
                        continue;
                    }
                }

                const thread = el("div", undefined, "comment-thread");
                thread.dataset.threadId = commentId;
                if (inReplyTo) {
                    const replies = el("div", undefined, "comment-replies");
                    replies.append(card);
                    thread.append(replies);
                } else {
                    thread.append(card);
                }
                entries.append(thread);
                threadMap.set(commentId, thread);
                if (comment.id && comment.id !== commentId) {
                    threadMap.set(comment.id, thread);
                }
            }
            offset += data.comments.length;
            onCoverage(
                !data.coverage_error &&
                    !!data.comments_synced_at &&
                    offset >= data.total,
            );
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
    const linkedIssues = [...(item.linked_issues || [])];
    const linkedPrs = [...(item.linked_prs || [])];

    if (
        item.kind === "pr" &&
        !linkedIssues.length &&
        item.raw &&
        item.raw.body
    ) {
        const pattern =
            /\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+(?:https?:\/\/github\.com\/[^\/\s]+\/[^\/\s]+\/issues\/|(?:[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)?#)(\d+)\b/gi;
        let match;
        while ((match = pattern.exec(item.raw.body)) !== null) {
            const num = Number(match[1]);
            if (!linkedIssues.includes(num)) linkedIssues.push(num);
        }
    }

    if (
        item.kind === "issue" &&
        !linkedPrs.length &&
        typeof pageItems !== "undefined" &&
        Array.isArray(pageItems)
    ) {
        for (const candidate of pageItems) {
            if (candidate.repo === item.repo && candidate.kind === "pr") {
                const candIssues = candidate.linked_issues || [];
                if (candIssues.includes(item.number)) {
                    if (!linkedPrs.includes(candidate.number))
                        linkedPrs.push(candidate.number);
                } else if (candidate.raw && candidate.raw.body) {
                    const pattern =
                        /\b(?:close|closes|closed|fix|fixes|fixed|resolve|resolves|resolved)\s+(?:https?:\/\/github\.com\/[^\/\s]+\/[^\/\s]+\/issues\/|(?:[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)?#)(\d+)\b/gi;
                    let match;
                    while (
                        (match = pattern.exec(candidate.raw.body)) !== null
                    ) {
                        if (
                            Number(match[1]) === item.number &&
                            !linkedPrs.includes(candidate.number)
                        ) {
                            linkedPrs.push(candidate.number);
                        }
                    }
                }
            }
        }
    }

    if (linkedIssues.length) {
        const box = el("div", undefined, "linked-items");
        box.append(el("strong", "Linked issues:"));
        for (const num of linkedIssues) {
            const found = pageItems.find(
                (i) => i.repo === item.repo && i.number === num,
            );
            const pill = el(
                "button",
                found ? `#${num} ${found.title}` : `#${num}`,
                "linked-item-pill",
            );
            pill.type = "button";
            pill.title = found ? found.title : `Issue #${num}`;
            pill.addEventListener("click", () => {
                if (found) {
                    renderDetail({ item: found, changes: [] });
                } else {
                    const base = item.url.replace(
                        /\/(pull|issues)\/\d+.*$/,
                        "",
                    );
                    window.open(
                        `${base}/issues/${num}`,
                        "_blank",
                        "noopener,noreferrer",
                    );
                }
            });
            box.append(pill);
        }
        main.append(box);
    } else if (linkedPrs.length) {
        const box = el("div", undefined, "linked-items");
        box.append(el("strong", "Linked pull requests:"));
        for (const num of linkedPrs) {
            const found = pageItems.find(
                (i) => i.repo === item.repo && i.number === num,
            );
            const pill = el(
                "button",
                found ? `#${num} ${found.title}` : `#${num}`,
                "linked-item-pill",
            );
            pill.type = "button";
            pill.title = found ? found.title : `Pull request #${num}`;
            pill.addEventListener("click", () => {
                if (found) {
                    renderDetail({ item: found, changes: [] });
                } else {
                    const base = item.url.replace(
                        /\/(pull|issues)\/\d+.*$/,
                        "",
                    );
                    window.open(
                        `${base}/pull/${num}`,
                        "_blank",
                        "noopener,noreferrer",
                    );
                }
            });
            box.append(pill);
        }
        main.append(box);
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
    let conversationReady = false;
    main.append(
        description,
        renderDiscussion(item, (ready) => {
            conversationReady = ready;
            updateFinish();
        }),
    );
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
    const priorityLabel = el("label", "Priority"),
        priority = el("select");
    priority.id = "triage-priority";
    priority.append(new Option("None / Default", ""));
    for (const [val, lab] of [
        ["urgent", "Urgent"],
        ["high", "High"],
        ["medium", "Medium"],
        ["low", "Low"],
    ])
        priority.append(new Option(lab, val));
    priority.value = item.priority || "";
    priorityLabel.htmlFor = priority.id;
    const categoryLabel = el("label", "Category"),
        category = el("select");
    category.id = "triage-category";
    category.append(new Option("Auto-infer", ""));
    for (const [val, lab] of [
        ["feature_request", "Feature Request"],
        ["feature_contribution", "Feature Contribution"],
        ["bug_report", "Bug Report"],
        ["bug_fix", "Bug Fix"],
        ["dependency_upgrade", "Dependency Upgrade"],
        ["documentation", "Documentation"],
        ["maintenance", "Maintenance"],
        ["ci", "CI/Automation"],
        ["question", "Question"],
        ["security", "Security"],
        ["other", "Other"],
    ])
        category.append(new Option(lab, val));
    category.value = item.category || "";
    categoryLabel.htmlFor = category.id;
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
    const finish = el("button", "Finish review & next →", "button primary");
    finish.type = "submit";
    const reviewHelp = el("p", undefined, "help-text");
    const processing = el(
        "p",
        item.processing_state === "processed"
            ? `Reviewed revision ${item.processed_revision} · ${item.processed_by} · ${formatDate(item.checked_at)}`
            : item.processing_state === "changed"
              ? "New activity since the last review."
              : "This item has not been reviewed yet.",
        "help-text",
    );
    function updateFinish() {
        const covered =
            item.available &&
            item.present &&
            !item.repo_error &&
            !!item.comments_synced_at;
        finish.disabled = saving || !covered || !conversationReady;
        reviewHelp.textContent = !covered
            ? "Sync complete item and conversation coverage before finishing. You can still save a decision."
            : !conversationReady
              ? "Load all comments and reviews before finishing this review."
              : "Finish saves your decision and acknowledges this inspected revision together. GitHub stays unchanged.";
    }
    const reload = button(
        "Reload source, keep edits",
        "button secondary",
        async () => {
            const edits = {
                disposition: status.value,
                priority: priority.value,
                category: category.value,
                note: note.value,
                until: until.value,
            };
            const token = detailGeneration;
            reload.disabled = true;
            try {
                const refreshed = await api(
                    `/api/items/${encodeURIComponent(item.id)}`,
                );
                if (token !== detailGeneration || !$("#detail").open) return;
                renderDetail(refreshed);
                for (const [key, selector] of Object.entries({
                    disposition: "#triage-disposition",
                    priority: "#triage-priority",
                    category: "#triage-category",
                    note: "#triage-note",
                    until: "#triage-until",
                })) {
                    $(selector).value = edits[key];
                    $(selector).dispatchEvent(
                        new Event(key === "note" ? "input" : "change", {
                            bubbles: true,
                        }),
                    );
                }
                notify(
                    "Source reloaded; your edits were kept. Review the refreshed content before finishing.",
                );
            } catch (failure) {
                error.textContent = failure.message;
                reload.disabled = false;
            }
        },
    );
    reload.type = "button";
    reload.hidden = true;
    form.append(
        statusLabel,
        status,
        priorityLabel,
        priority,
        categoryLabel,
        category,
        snooze,
        noteLabel,
        note,
        notePreview,
        error,
        save,
        saveNext,
        processing,
        finish,
        reviewHelp,
        reload,
    );
    updateFinish();
    form.addEventListener("input", () => {
        dirty = true;
    });
    form.addEventListener("change", () => {
        dirty = true;
    });
    form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (saving) return;
        const finishing = event.submitter === finish;
        if (finishing && finish.disabled) return;
        const advance = event.submitter === saveNext || finishing;
        let advanceId = nextId;
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
            // Freeze the next candidate before this write can shrink a filtered page.
            if (
                finishing &&
                !advanceId &&
                view !== "board" &&
                activeListQuery &&
                offset + pageItems.length < total
            ) {
                const nextQuery = new URLSearchParams(activeListQuery);
                nextQuery.set("offset", offset + pageItems.length);
                nextQuery.set("limit", 1);
                advanceId = (await api(`/api/items?${nextQuery}`)).items[0]?.id;
            }
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
                        priority: priority.value || null,
                        category: category.value || null,
                        snoozed_until: deadline,
                        revision: item.revision,
                        finish_review: finishing,
                        actor: "dashboard",
                    }),
                },
            );
            dirty = false;
            saving = false;
            $("#close-detail").disabled = false;
            renderDetail(detail);
            notify(
                finishing
                    ? "Review finished. Decision and inspected revision saved."
                    : "Decision saved to your local workspace.",
            );
            await refresh();
            if (savedGeneration !== detailGeneration || !$("#detail").open)
                return;
            if (dirty || saving) return;
            if (advance && advanceId) {
                if (
                    view !== "board" &&
                    !pageItems.some((row) => row.id === advanceId)
                ) {
                    offset += limit;
                    await loadItems();
                    if (
                        savedGeneration !== detailGeneration ||
                        !$("#detail").open ||
                        dirty ||
                        saving
                    )
                        return;
                }
                showDetail(advanceId);
            } else if (finishing) $("#detail").close();
            else updateDetailNavigation();
        } catch (failure) {
            error.textContent = failure.message;
            error.hidden = false;
            saving = false;
            $("#close-detail").disabled = false;
            for (const control of form.elements) control.disabled = false;
            saveNext.disabled = !nextId;
            reload.hidden = failure.status !== 409;
            updateFinish();
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
    $("#state").value = ["all", "board"].includes(view) ? "" : "open";
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
    $("#state").value = ["all", "board"].includes(view) ? "" : "open";
    $("#sort").value = "priority";
    if ($("#category")) $("#category").value = "";
    if ($("#priority")) $("#priority").value = "";
    if ($("#triaged")) $("#triaged").value = "";
    if ($("#author")) $("#author").value = "";
    if ($("#since")) $("#since").value = "";
    if ($("#until")) $("#until").value = "";
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
function syncSelectionFields() {
    $("#sync-owner-field").hidden = $("#sync-selection").value !== "owner";
    $("#sync-repos-field").hidden = $("#sync-selection").value !== "repos";
    $("#sync-owner").required = !$("#sync-owner-field").hidden;
    $("#sync-repos").required = !$("#sync-repos-field").hidden;
}
function renderSyncStatus(status) {
    dashboardSync = status;
    const running = status.state === "running";
    for (const control of $("#sync-form").elements) control.disabled = running;
    $("#sync-start").disabled = running || !credentialAvailable;
    $("#sync-start").textContent = ["failed", "partial", "cancelled"].includes(
        status.state,
    )
        ? "Retry sync"
        : "Start sync";
    $("#sync-cancel").hidden = !running;
    $("#sync-cancel").disabled = false;
    $("#sync-open").textContent = running ? "Syncing…" : "Sync now";
    $("#sync-progress").textContent =
        `${status.message}${status.stages ? ` · ${status.stages} stages observed` : ""}`;
    $("#sync-errors").replaceChildren(
        ...(status.result?.errors || []).map((error) => el("li", error)),
    );
    if (running)
        $("#sync-status").textContent = `Syncing GitHub · ${status.message}`;
    else if (["failed", "partial", "cancelled"].includes(status.state)) {
        $("#sync-status").textContent =
            `${status.message} Open Sync now for details and retry.`;
        $("#sync-status").classList.add("warn");
    }
}
function scheduleSyncPoll() {
    clearTimeout(syncPoll);
    if (dashboardSync?.state === "running")
        syncPoll = setTimeout(pollSync, 1000);
}
async function pollSync() {
    try {
        const status = await api("/api/sync");
        const wasRunning = dashboardSync?.state === "running";
        renderSyncStatus(status);
        if (wasRunning && status.state !== "running") {
            await refresh();
            renderSyncStatus(status);
            notify(
                status.state === "complete"
                    ? "GitHub sync completed. Your index is up to date."
                    : status.message,
            );
        }
    } catch (error) {
        $("#sync-progress").textContent =
            `Could not read sync progress: ${error.message} Reconnecting…`;
    }
    scheduleSyncPoll();
}
async function loadSetup(applySelection = false) {
    $("#auth-check").disabled = true;
    try {
        const data = await api("/api/setup");
        credentialAvailable = data.credential_available;
        $("#credential-status").textContent = data.message;
        $("#credential-status").classList.toggle(
            "signal-warning",
            !credentialAvailable,
        );
        if (applySelection) {
            syncOptions = data.sync.options;
            $("#sync-selection").value = syncOptions.repo?.length
                ? "repos"
                : syncOptions.owner
                  ? "owner"
                  : "mine";
            $("#sync-owner").value = syncOptions.owner || "";
            $("#sync-repos").value = (syncOptions.repo || []).join("\n");
            $("#sync-full").checked = syncOptions.full;
            syncSelectionFields();
        }
        renderSyncStatus(data.sync);
        scheduleSyncPoll();
    } catch (error) {
        $("#credential-status").textContent =
            `Could not check authentication: ${error.message}`;
        $("#sync-start").disabled = true;
    } finally {
        $("#auth-check").disabled = false;
    }
}
function openSync() {
    if (!$("#sync-dialog").open) $("#sync-dialog").showModal();
    loadSetup();
}
$("#sync-open").addEventListener("click", openSync);
$("#setup-open").addEventListener("click", openSync);
$("#sync-close").addEventListener("click", () => $("#sync-dialog").close());
$("#auth-check").addEventListener("click", () => loadSetup());
$("#sync-selection").addEventListener("change", syncSelectionFields);
$("#sync-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (syncSubmitting || dashboardSync?.state === "running") return;
    $("#sync-error").hidden = true;
    const mode = $("#sync-selection").value;
    const options = {
        ...syncOptions,
        owner: mode === "owner" ? $("#sync-owner").value.trim() : null,
        repo:
            mode === "repos"
                ? $("#sync-repos")
                      .value.split(/[\n,]+/)
                      .map((name) => name.trim())
                      .filter(Boolean)
                : null,
        full: $("#sync-full").checked,
    };
    syncSubmitting = true;
    $("#sync-start").disabled = true;
    try {
        const status = await api("/api/sync", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Maintainer-Request": "1",
            },
            body: JSON.stringify(options),
        });
        syncOptions = status.options;
        renderSyncStatus(status);
        scheduleSyncPoll();
    } catch (error) {
        $("#sync-error").textContent = error.message;
        $("#sync-error").hidden = false;
        $("#sync-start").disabled = !credentialAvailable;
    } finally {
        syncSubmitting = false;
    }
});
$("#sync-cancel").addEventListener("click", async () => {
    $("#sync-cancel").disabled = true;
    try {
        renderSyncStatus(
            await api("/api/sync/cancel", {
                method: "POST",
                headers: { "X-Maintainer-Request": "1" },
            }),
        );
        await refresh();
        renderSyncStatus(dashboardSync);
    } catch (error) {
        $("#sync-error").textContent = error.message;
        $("#sync-error").hidden = false;
        $("#sync-cancel").disabled = false;
    }
});
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
let authorTimer;
$("#author")?.addEventListener("input", () => {
    clearTimeout(authorTimer);
    authorTimer = setTimeout(() => {
        offset = 0;
        loadItems();
    }, 180);
});
for (const selector of [
    "#repo",
    "#state",
    "#sort",
    "#category",
    "#priority",
    "#triaged",
    "#since",
    "#until",
])
    $(selector)?.addEventListener("change", () => {
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
    if (event.key === "/" && !$("#detail").open && !$("#sync-dialog").open) {
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
loadSetup(true);
