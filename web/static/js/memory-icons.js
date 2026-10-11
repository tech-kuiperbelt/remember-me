// Icons shared by the Memory list and drawer; paths match Stats Overview.
export function createMemoryIcon(dimension) {
  const paths = {
    memory: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Z M14 2v6h6 M8 13h8 M8 17h6',
    namespace: 'm12 3 9 5-9 5-9-5 9-5Z M3 12l9 5 9-5 M3 16l9 5 9-5',
    category: 'M20 13 11 22 2 13V3h10l8 8a2 2 0 0 1 0 2Z M7 7h.01',
    keyword: 'M4 9h16 M3 15h16 M10 3 6 21 M18 3l-4 18',
    anchor: 'M12 8v13 M8 12H4a8 8 0 0 0 16 0h-4 M9 5a3 3 0 1 0 6 0 3 3 0 0 0-6 0Z',
  };
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [name, value] of Object.entries({viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 1.8, 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true', focusable: 'false'})) svg.setAttribute(name, value);
  svg.classList.add('memory-metadata-icon', 'memory-' + dimension + '-icon');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', paths[dimension]); svg.append(path);
  return svg;
}
