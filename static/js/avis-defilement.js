(() => {
  const carousel = document.querySelector("[data-testimonial-carousel]");
  if (!carousel) return;

  const track = carousel.querySelector("[data-testimonial-track]");
  const cards = Array.from(track.querySelectorAll(".testimonial-slide"));
  const controls = carousel.querySelector("[data-testimonial-controls]");
  const previous = carousel.querySelector("[data-testimonial-prev]");
  const next = carousel.querySelector("[data-testimonial-next]");
  const position = carousel.querySelector("[data-testimonial-position]");
  if (!track || cards.length < 2 || !controls || !previous || !next || !position) return;

  controls.hidden = false;
  let index = 0;
  let timer = null;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  const cardStep = () => cards[0].getBoundingClientRect().width + parseFloat(getComputedStyle(track).gap || "0");
  const update = () => {
    index = Math.max(0, Math.min(cards.length - 1, Math.round(track.scrollLeft / cardStep())));
    position.textContent = `${index + 1} / ${cards.length}`;
  };
  const show = (nextIndex) => {
    index = (nextIndex + cards.length) % cards.length;
    track.scrollTo({ left: index * cardStep(), behavior: reducedMotion.matches ? "auto" : "smooth" });
    position.textContent = `${index + 1} / ${cards.length}`;
  };
  const stop = () => {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
  };
  const start = () => {
    stop();
    if (reducedMotion.matches || document.hidden) return;
    timer = window.setInterval(() => show(index + 1), 6000);
  };

  previous.addEventListener("click", () => { show(index - 1); start(); });
  next.addEventListener("click", () => { show(index + 1); start(); });
  track.addEventListener("scroll", update, { passive: true });
  carousel.addEventListener("pointerenter", stop);
  carousel.addEventListener("pointerleave", start);
  carousel.addEventListener("focusin", stop);
  carousel.addEventListener("focusout", (event) => {
    if (!carousel.contains(event.relatedTarget)) start();
  });
  document.addEventListener("visibilitychange", () => document.hidden ? stop() : start());
  reducedMotion.addEventListener?.("change", start);
  update();
  start();
})();
