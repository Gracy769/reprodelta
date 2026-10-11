(() => {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || !window.IntersectionObserver) return;
  const targets = document.querySelectorAll(".home-section, .workflow article, .footer-cta");
  targets.forEach((item) => item.classList.add("reveal-target"));
  document.documentElement.classList.add("motion-enabled");
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      entry.target.classList.add("in-view");
      observer.unobserve(entry.target);
    });
  }, { threshold: 0.12, rootMargin: "0px 0px -30px 0px" });
  targets.forEach((item) => observer.observe(item));
})();
