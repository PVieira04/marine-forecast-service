// Cron is UTC-only, so the Worker fires at both candidate UTC hours per slot
// (05/06 and 17/18) and only dispatches when it is 07:00 or 19:00 in Madrid.
export function slotFor(date) {
  const hour = Number(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: "Europe/Madrid",
      hour: "numeric",
      hourCycle: "h23",
    }).format(date),
  );
  if (hour === 7) return "morning";
  if (hour === 19) return "evening";
  return null;
}
