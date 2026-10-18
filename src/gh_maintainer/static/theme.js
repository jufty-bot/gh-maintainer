(() => {
    const root = document.documentElement;
    const system = window.matchMedia("(prefers-color-scheme: dark)");
    const storageKey = "maintainer-theme";
    let preference;
    try {
        const saved = localStorage.getItem(storageKey);
        if (saved === "light" || saved === "dark") preference = saved;
    } catch {}

    function apply(theme) {
        root.dataset.theme = theme;
        document.querySelector('meta[name="theme-color"]').content =
            theme === "dark" ? "#282420" : "#f7f4ee";
        const button = document.querySelector("#theme-toggle");
        if (button) {
            const label = `Switch to ${theme === "dark" ? "light" : "dark"} theme`;
            button.textContent = theme === "dark" ? "☼" : "☾";
            button.setAttribute("aria-label", label);
            button.title = label;
        }
    }

    apply(preference || (system.matches ? "dark" : "light"));
    system.addEventListener("change", () => {
        if (!preference) apply(system.matches ? "dark" : "light");
    });
    document.addEventListener("DOMContentLoaded", () => {
        apply(root.dataset.theme);
        document
            .querySelector("#theme-toggle")
            .addEventListener("click", () => {
                preference = root.dataset.theme === "dark" ? "light" : "dark";
                apply(preference);
                try {
                    localStorage.setItem(storageKey, preference);
                } catch {}
            });
    });
})();
