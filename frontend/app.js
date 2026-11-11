const rows = document.querySelector('#items');
const status = document.querySelector('#status');
const button = document.querySelector('#refresh');

async function loadItems() {
  button.disabled = true;
  status.textContent = 'Загрузка данных…';
  try {
    const response = await fetch('/api/items', { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const items = await response.json();
    rows.replaceChildren();
    for (const item of items) {
      const row = document.createElement('tr');
      for (const value of [item.id, item.name]) {
        const cell = document.createElement('td');
        cell.textContent = value;
        row.appendChild(cell);
      }
      rows.appendChild(row);
    }
    status.textContent = `Получено записей: ${items.length}. Подключение к базе работает.`;
  } catch (error) {
    rows.replaceChildren();
    status.textContent = `Не удалось получить данные: ${error.message}`;
  } finally {
    button.disabled = false;
  }
}

button.addEventListener('click', loadItems);
loadItems();
