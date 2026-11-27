const time = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' });
const weekday = new Intl.DateTimeFormat('ru-RU', { weekday: 'long' });
const day = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' });

// «Сегодня, 14:02», «вчера», «четверг», «21 нояб.» — как в макете.
export function ticketDate(value: string, now = new Date()) {
  const date = new Date(value);
  const startOfDay = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOfDay(now) - startOfDay(date)) / 86_400_000);
  if (days <= 0) return `Сегодня, ${time.format(date)}`;
  if (days === 1) return 'вчера';
  if (days < 7) return weekday.format(date);
  return day.format(date);
}
