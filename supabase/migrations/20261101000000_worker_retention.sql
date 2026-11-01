select cron.schedule(
  'task-queue-cleanup-workers',
  '0 * * * *',
  $$ delete from workers where last_heartbeat_at < now() - interval '30 days' $$
);
