conceptMap('cmap', 'cmap-spec');

// The note's formula with the prefactor set to 1. The check calls the same function, so it fails if
// the plotted expression changes: doubling ℓt multiplies ξ by 2e^{π²ℓt}, which follows from the formula.
const xi = l => l * Math.exp(Math.PI ** 2 * l);
check(Math.abs(Math.log(xi(1) / xi(0.5)) - (Math.log(2) + Math.PI ** 2 / 2)) < 1e-9, 'xi: ξ(2ℓ)/ξ(ℓ) = 2e^{π²ℓ}');
livePlot('xi-plot', {
  formula: String.raw`\xi\sim\ell_t\,e^{\pi^2\ell_t/\lambda_e}`, src: 'Alpha',
  sliders: [{ key: 'l', label: 'ℓt / λe', min: 0.2, max: 1, step: 0.05, value: 0.5, fmt: v => v.toFixed(2) }],
  traces: p => {
    const x = Array.from({ length: 60 }, (_, i) => 0.1 + (1.9 * i) / 59);
    return [{ x, y: x.map(xi), mode: 'lines', name: 'ξ/λe' }, { x: [p.l, 2 * p.l], y: [xi(p.l), xi(2 * p.l)], mode: 'markers', name: 'ℓt and 2ℓt' }];
  },
  layout: () => ({ xaxis: { title: 'ℓt / λe' }, yaxis: { title: 'ξ / λe', type: 'log' } }),
  readout: p => `doubling ℓt multiplies ξ by ${Math.round(xi(2 * p.l) / xi(p.l))}`,
});
