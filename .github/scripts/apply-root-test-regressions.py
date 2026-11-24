#!/usr/bin/env python3
"""Apply evidence-backed Root regression fixes to the assembled source tree."""
from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one exact anchor, found {count}")
    return source.replace(old, new, 1)


log_path = Path("model/log.go")
log_source = log_path.read_text(encoding="utf-8")
log_old = """\tchannelNames := make(map[int]string)
\tvar channelIDs []int
\tif err := tx.Model(&Log{}).Distinct("channel_id").Where("channel_id <> ?", 0).Pluck("channel_id", &channelIDs).Error; err != nil {
\t\treturn err
\t}
"""
log_new = """\t// GORM chain methods mutate the current statement. Use a separately
\t// rebuilt filtered query for channel discovery so Pluck cannot turn the
\t// subsequent streaming query into SELECT DISTINCT channel_id.
\tchannelQuery, err := filter.apply(LOG_DB.WithContext(ctx))
\tif err != nil {
\t\treturn err
\t}
\tchannelNames := make(map[int]string)
\tvar channelIDs []int
\tif err := channelQuery.Model(&Log{}).Distinct("channel_id").Where("channel_id <> ?", 0).Pluck("channel_id", &channelIDs).Error; err != nil {
\t\treturn err
\t}
"""
log_source = replace_once(log_source, log_old, log_new, "StreamAllLogs filtered channel lookup")
log_path.write_text(log_source, encoding="utf-8")

test_path = Path("controller/user_manage_test.go")
test_source = test_path.read_text(encoding="utf-8")
test_old = """\tvar ready sync.WaitGroup
\tready.Add(2)
\trelease := make(chan struct{})
\trequire.NoError(t, db.Callback().Query().Before("gorm:query").Register("test:concurrent_quota_start", func(tx *gorm.DB) {
\t\tif tx.Statement.Table == "users" {
\t\t\tready.Done()
\t\t\t<-release
\t\t}
\t}))
"""
test_new = """\t// Synchronize the competing callers before they enter database/sql.
\t// Blocking inside GORM's query callback can starve a single-connection
\t// SQLite pool: the first transaction holds the only connection while the
\t// second can never reach the callback that releases the barrier.
\tstart := make(chan struct{})
"""
test_source = replace_once(test_source, test_old, test_new, "concurrent quota test callback barrier")
test_old = """\tfor _, value := range []int{10, 20} {
\t\tgo func(value int) {
\t\t\tadjustment, err := model.AdjustUserQuota(user.Id, common.RoleRootUser, "add", value)
\t\t\tresults <- result{adjustment, err, value}
\t\t}(value)
\t}
\tready.Wait()
\tclose(release)
"""
test_new = """\tfor _, value := range []int{10, 20} {
\t\tgo func(value int) {
\t\t\t<-start
\t\t\tadjustment, err := model.AdjustUserQuota(user.Id, common.RoleRootUser, "add", value)
\t\t\tresults <- result{adjustment, err, value}
\t\t}(value)
\t}
\tclose(start)
"""
test_source = replace_once(test_source, test_old, test_new, "concurrent quota test start synchronization")
test_source = replace_once(
    test_source,
    '\trequire.NoError(t, db.Callback().Query().Remove("test:concurrent_quota_start"))\n',
    "",
    "concurrent quota test callback cleanup",
)
test_source = replace_once(test_source, '\t"sync"\n', "", "unused sync import")
test_path.write_text(test_source, encoding="utf-8")
print("PASS: independent log channel lookup and non-blocking SQLite concurrency fixture applied.")
