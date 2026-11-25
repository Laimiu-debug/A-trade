document.querySelector('button').addEventListener('click', () => window.close());
document.addEventListener('keydown', e => { if (e.key === 'Escape') window.close(); });
