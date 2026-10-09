/* Dates and durations follow the instance language and time zone. */
(() => {
  function formatTurns() {
    const table = document.getElementById('turn-list');
    if (!table) return;
    const locale = table.dataset.locale;
    const timeZone = table.dataset.timezone;
    const day = new Intl.DateTimeFormat(locale, {day: '2-digit', month: '2-digit', year: 'numeric', timeZone});
    const clock = new Intl.DateTimeFormat(locale, {hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23', timeZone, timeZoneName: 'short'});
    table.querySelectorAll('time[datetime]').forEach(node => {
      const date = new Date(node.dateTime);
      if (!Number.isFinite(date.getTime())) return;
      const dateText = document.createElement('span');
      dateText.textContent = day.format(date);
      const clockText = document.createElement('small');
      clockText.textContent = clock.format(date);
      node.replaceChildren(dateText, clockText);
      node.title = node.dateTime;
    });
    const unit = (value, name, digits = 0) => new Intl.NumberFormat(locale, {style: 'unit', unit: name, unitDisplay: 'short', maximumFractionDigits: digits}).format(value);
    table.querySelectorAll('[data-duration]').forEach(node => {
      const seconds = Number(node.dataset.duration);
      if (!Number.isFinite(seconds) || seconds < 0) return;
      if (seconds < 60) { node.textContent = unit(seconds, 'second', 1); return; }
      const total = Math.floor(seconds);
      const parts = [];
      if (total >= 3600) parts.push(unit(Math.floor(total / 3600), 'hour'));
      if (Math.floor(total / 60) % 60) parts.push(unit(Math.floor(total / 60) % 60, 'minute'));
      if (total % 60) parts.push(unit(total % 60, 'second'));
      node.textContent = parts.join(' ');
    });
  }
  document.addEventListener('DOMContentLoaded', formatTurns);
  document.addEventListener('htmx:afterSwap', formatTurns);
  if (document.readyState !== 'loading') formatTurns();
})();
