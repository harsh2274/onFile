async function loadDashboard() {
  const response = await fetch('/api/dashboard');
  const data = await response.json();

  const cards = document.getElementById('cards');
  const entries = Object.entries(data.totals);
  cards.innerHTML = entries.map(([key, value]) => `
    <article class="card">
      <h3>${key.replace(/_/g, ' ')}</h3>
      <strong>${value}</strong>
    </article>
  `).join('');

  document.getElementById('audit').innerHTML = (data.recent_activity || []).slice(0, 5).map(item => `
    <li>${item.action} / ${item.entity_type} / ${item.created_at}</li>
  `).join('');

  const integration = document.getElementById('integration');
  integration.innerHTML = `
    <div>NICRRON: ${data.integration.nicrron}</div>
    <div>PRISM: ${data.integration.prism}</div>
    <div>Mock mode: ${data.integration.mock_mode ? 'true' : 'false'}</div>
  `;
}

loadDashboard();
