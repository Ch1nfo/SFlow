# SentinelFlow 大库性能与数据生命周期

本文档说明 `sys_queue.db` 在大体量告警任务场景下的性能优化要点、观测方式与清理策略。

## 性能优化摘要

- **列表接口**（`GET /api/sentinelflow/alerts/state`）通过 `list_task_rows()` 只读取轻量列，不再 `SELECT` `payload` / `last_result_data`；默认 `limit=120`。
- **Dashboard**（`GET /api/sentinelflow/dashboard/summary`）通过 `dashboard_aggregates()` 使用 SQL 聚合与 denormalized 列，禁止 `list_tasks()` 全表 Python 解析。
- **排序**使用可索引列 `sort_time`（写入时维护为 `alert_time` 或 `updated_at`），索引：`idx_alert_tasks_sort_time`、`idx_alert_tasks_source_sort`。
- **Layout 新告警提示**使用 `GET /api/sentinelflow/alerts/state/headlines`（默认最多 200 条轻量 headline），避免为 toast 拉取全量任务列表。
- **本周研判/封禁摘要**使用 `GET /api/sentinelflow/alerts/summary/period?since=<iso>&sourceId=all`，按时间范围 SQL 聚合全量任务，不复用 `/alerts/state` 列表首屏数据。
- **Poller** 启动与 `poll_once` 不再附带全量 `tasks`；状态计数来自 `task_status_counts()`。
- **封禁 IP 聚合**在 `schema_meta.alert_tasks_banned_ips_backfill_v1=completed` 后只读取 `banned_ips` 列；历史数据在低峰首次启动时一次性 backfill，不在 Dashboard/周摘要热路径回退解析 `last_result_data`。
- **历史 JSON 诊断**由独立标记 `schema_meta.alert_tasks_invalid_result_json_audit_v1=completed` 控制；升级后首次启动扫描并记录非法结果 JSON 样本，后续启动不重复扫描大字段。

## 生产 EXPLAIN 验证

在低峰期对生产库执行（路径以实际 `CONFIG_DIR/sys_queue.db` 为准）：

```sql
EXPLAIN QUERY PLAN
SELECT task_id, event_ids, workflow_name, title, description, source_id, source_name,
       alert_time, updated_at, sort_time, status, retry_count, last_action,
       last_result_success, last_result_error, disposition, outcome_status,
       banned_ips, result_summary
FROM alert_tasks
ORDER BY sort_time DESC
LIMIT 120;
```

期望计划中出现 `USING INDEX idx_alert_tasks_sort_time`（或 source 过滤时使用 `idx_alert_tasks_source_sort`），避免 `SCAN alert_tasks` + `USE TEMP B-TREE FOR ORDER BY`。

Dashboard 聚合可抽查：

```sql
EXPLAIN QUERY PLAN
SELECT status, COUNT(*) AS count FROM alert_tasks GROUP BY status;
```

## 观测日志

以下路径会输出耗时日志（`duration_ms`、`row_count`、`source_id` 等）：

- `dispatch_service.list_task_rows`
- `dispatch_service.dashboard_aggregates`
- `dispatch_service.list_task_headlines`
- `alerts_state` / `alerts_state_tasks`

可在应用日志中检索 `dispatch_query` 与 `alerts_state` 关键字。

## 数据清理策略

1. **Weekly cleanup**：平台内置 `WeeklyAlertCleanupService`，通过 `delete_tasks_before()` 删除 cutoff 之前的任务。生产环境应确认调度已启用（见平台设置 / 运行时配置）。
2. **避免无限保留 demo/测试源**：演示模式任务会在无新 demo 告警时被 `clear_demo_tasks()` 清理；测试环境应定期手动清理或缩短保留窗口。
3. **大库迁移/backfill**：新增 `sort_time` / denormalized 列时会在 schema 迁移中执行一次性 `UPDATE`；`banned_ips` 回填与非法 JSON 诊断分别由独立 `schema_meta` 标记控制，仅首次低峰启动扫描 `last_result_data`，成功后常规重启不再重复。对 300MB+ 库建议在低峰执行，并确保 SQLite `busy_timeout` 足够（见 `sqlite_support`）。
4. **可选归档**（后续）：对已完成任务 N 天后清空 `payload` / `last_result_data`，仅保留 denormalized 列与审计所需字段，可进一步控制库体积。

## 回归测试

```bash
cd runtime
python -m pytest tests/
```

覆盖：列表/聚合热路径不读大列、Dashboard 不调用 `list_tasks()`、写入时维护 denormalized 字段、headlines 轻量返回，以及一次性迁移诊断不重复扫描历史 JSON。
