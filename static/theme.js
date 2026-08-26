(() => {
  const KEY = "el_theme";
  const BAR = { "": "#f3f1ec", slate: "#eef1f4", dark: "#14171a" };
  const ORDER = ["", "slate", "dark"];
  const locked = document.documentElement.dataset.themeLocked === "true";
  function stored() { try { return localStorage.getItem(KEY) ?? ""; } catch { return ""; } }
  function apply(name) {
    const requested = Object.prototype.hasOwnProperty.call(BAR, name) ? name : "";
    const theme = locked ? "dark" : requested;
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute("content", BAR[theme]);
    if (!locked) { try { localStorage.setItem(KEY, theme); } catch {} }
    window.dispatchEvent(new CustomEvent("el:theme", { detail: theme }));
  }
  function current() { return locked ? "dark" : stored(); }
  apply(current());
  window.ELTheme = { apply, current, cycle() { apply(ORDER[(ORDER.indexOf(current()) + 1) % ORDER.length]); } };
  document.addEventListener("click", (event) => {
    if (event.target.closest?.("[data-cycle-theme]")) window.ELTheme.cycle();
  });
})();
