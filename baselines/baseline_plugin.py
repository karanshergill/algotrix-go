"""Base class and shared helpers for baseline computation plugins."""

import json
import math
import socket
import urllib.parse
import urllib.request

import psycopg2


def questdb_query(cfg, sql):
    """Query QuestDB via HTTP API, return list of dicts."""
    host = cfg["questdb"]["host"]
    port = cfg["questdb"]["http_port"]
    url = f"http://{host}:{port}/exec?query={urllib.parse.quote(sql)}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=600) as resp:
        data = json.loads(resp.read().decode())
    if "error" in data:
        raise RuntimeError(f"QuestDB error: {data['error']}")
    columns = [c["name"] for c in data["columns"]]
    return [dict(zip(columns, row)) for row in data.get("dataset", [])]


def questdb_write_ilp(cfg, table, rows, symbols, timestamps):
    """Write rows to QuestDB via ILP (line protocol) over TCP.

    Args:
        cfg: parsed config dict with questdb.host and questdb.ilp_port
        table: target table name
        rows: list of dicts, each dict is one row of field values
        symbols: list of column names that are SYMBOL type (sent as tags)
        timestamps: list of column names that hold the designated timestamp
                    (only the first is used as the line-protocol timestamp;
                     the value must be an int in nanoseconds)
    Returns:
        number of rows written
    """
    host = cfg["questdb"]["host"]
    port = cfg["questdb"]["ilp_port"]

    ts_col = timestamps[0] if timestamps else None
    symbol_set = set(symbols)
    ts_set = set(timestamps)

    lines = []
    for row in rows:
        # Tags (symbol columns)
        tag_parts = []
        for col in symbols:
            val = row.get(col)
            if val is not None:
                # Symbol values: no quoting, escape spaces/commas/equals
                sval = str(val).replace(" ", "\\ ").replace(",", "\\,").replace("=", "\\=")
                tag_parts.append(f"{col}={sval}")

        # Fields (non-symbol, non-timestamp columns)
        field_parts = []
        for col, val in row.items():
            if col in symbol_set or col in ts_set:
                continue
            if val is None:
                continue
            if isinstance(val, bool):
                field_parts.append(f"{col}={'t' if val else 'f'}")
            elif isinstance(val, int):
                field_parts.append(f"{col}={val}i")
            elif isinstance(val, float):
                if not math.isfinite(val):
                    continue
                field_parts.append(f"{col}={val}")
            elif isinstance(val, str):
                # Escape double quotes inside string values
                escaped = val.replace("\\", "\\\\").replace('"', '\\"')
                field_parts.append(f'{col}="{escaped}"')
            else:
                field_parts.append(f"{col}={val}")

        # Timestamp in nanoseconds
        ts_val = ""
        if ts_col and ts_col in row and row[ts_col] is not None:
            ts_val = f" {int(row[ts_col])}"

        tag_str = "," + ",".join(tag_parts) if tag_parts else ""
        field_str = ",".join(field_parts) if field_parts else ""
        if not field_str:
            continue
        line = f"{table}{tag_str} {field_str}{ts_val}\n"
        lines.append(line)

    if not lines:
        return 0

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(60)
    try:
        sock.connect((host, port))
        # Send in chunks to avoid huge buffers
        chunk_size = 1000
        for i in range(0, len(lines), chunk_size):
            chunk = "".join(lines[i:i + chunk_size])
            sock.sendall(chunk.encode("utf-8"))
        sock.shutdown(socket.SHUT_WR)
    finally:
        sock.close()

    return len(lines)


def pg_query(cfg, sql):
    """Query PostgreSQL, return list of dicts."""
    pc = cfg["postgres"]
    conn = psycopg2.connect(
        host=pc["host"], port=pc["port"],
        user=pc["user"], password=pc["password"],
        database=pc["database"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            columns = [d[0] for d in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


class BaselinePlugin:
    """Base class for all baseline computation plugins."""

    name = ""
    description = ""
    dependencies = []

    def __init__(self, cfg):
        """cfg = full parsed YAML config dict"""
        self.cfg = cfg
        self.plugin_cfg = cfg["baselines"][self.name]

    def enabled(self):
        return self.plugin_cfg.get("enabled", True)

    def validate_config(self):
        """Check required config keys exist. Raise on error."""
        raise NotImplementedError

    def compute(self):
        """Run the computation. Return results (list of dicts or similar)."""
        raise NotImplementedError

    def store(self, results):
        """Write results to QuestDB. Return row count written."""
        raise NotImplementedError
