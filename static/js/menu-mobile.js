(() => {
  const header = document.querySelector(".site-header");
  const button = header?.querySelector(".menu-toggle");
  const navigation = header?.querySelector(".main-nav");
  if (!header || !button || !navigation) return;

  navigation.id = "main-navigation";
  const close = () => {
    header.classList.remove("menu-open");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", "Ouvrir le menu");
  };
  button.addEventListener("click", () => {
    const isOpen = button.getAttribute("aria-expanded") === "true";
    header.classList.toggle("menu-open", !isOpen);
    button.setAttribute("aria-expanded", String(!isOpen));
    button.setAttribute("aria-label", isOpen ? "Ouvrir le menu" : "Fermer le menu");
  });
  navigation.addEventListener("click", (event) => {
    if (event.target.closest("a")) close();
  });
  document.addEventListener("click", (event) => {
    if (!header.contains(event.target)) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      close();
      button.focus();
    }
  });
})();
