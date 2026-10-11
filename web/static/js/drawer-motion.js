// Shared right-side drawer motion.
export function createDrawerAnimator(overlay, panel) {
  let drawerAnimations = [];
  return function animateDrawer(open) {
    drawerAnimations.forEach(animation => animation.cancel());
    drawerAnimations = [];
    overlay.inert = !open;
    overlay.style.pointerEvents = open ? '' : 'none';
    if (open) overlay.style.display = 'flex';
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      if (!open) overlay.style.display = 'none';
      return;
    }
    const options = {duration: open ? 240 : 200, easing: 'cubic-bezier(0.22, 1, 0.36, 1)', fill: 'both'};
    const animations = [
      panel.animate([{transform: open ? 'translateX(100%)' : 'translateX(0)'},
        {transform: open ? 'translateX(0)' : 'translateX(100%)'}], options),
      overlay.animate([{opacity: open ? 0 : 1}, {opacity: open ? 1 : 0}], options)
    ];
    drawerAnimations = animations;
    Promise.all(animations.map(animation => animation.finished)).then(() => {
      if (drawerAnimations !== animations) return;
      if (!open) overlay.style.display = 'none';
      animations.forEach(animation => animation.cancel());
      drawerAnimations = [];
    }).catch(() => {});
  };
}
