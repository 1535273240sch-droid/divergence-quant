-- DivergenceQuant TimescaleDB 时序数据库初始化脚本
-- 启用 timescaledb 插件，并将 candles 表配置为时序超表与自动压缩

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- 确保在 alembic 迁移后将 candles 表转换为超表
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'candles'
    ) THEN
        -- 按 ts_ms (毫秒时间戳) 分区，每 1 天 (86,400,000 ms) 为一个 chunk
        PERFORM create_hypertable(
            'candles',
            'ts_ms',
            chunk_time_interval => 86400000,
            if_not_exists => TRUE
        );

        -- 启用超表压缩策略，依据 inst_id, bar 进行分段压缩，按 ts_ms 倒序排布
        ALTER TABLE candles SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'inst_id,bar',
            timescaledb.compress_orderby = 'ts_ms DESC'
        );

        -- 自动压缩策略：将 7 天以前的历史数据分块自动压缩
        PERFORM add_compression_policy('candles', INTERVAL '7 days', if_not_exists => TRUE);
    END IF;
END $$;
