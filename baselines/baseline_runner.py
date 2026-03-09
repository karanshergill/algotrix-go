#!/usr/bin/env python3
"""Baseline computation orchestrator.

Usage:
    python3 baselines/baseline_runner.py --config baselines/baseline_config.yaml
    python3 baselines/baseline_runner.py --config baselines/baseline_config.yaml --only volume_profile,daily_cvd
    python3 baselines/baseline_runner.py --config baselines/baseline_config.yaml --symbols INE001,INE002
    python3 baselines/baseline_runner.py --config baselines/baseline_config.yaml --dry-run
"""

import argparse
import importlib
import os
import sys
import time
from datetime import datetime

import yaml

# Allow importing data/health modules
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "health"))

from baselines.baseline_plugin import BaselinePlugin


def load_config(path):
    with open(path) as f:
        return yaml.safe_load(f)


def discover_plugins(cfg):
    """Discover and load all plugin classes from baselines/ directory.

    Convention: file name matches the plugin name in config.
    Each file must define a class that subclasses BaselinePlugin.
    """
    baselines_dir = os.path.dirname(os.path.abspath(__file__))
    plugins = {}

    for plugin_name in cfg.get("baselines", {}):
        module_path = os.path.join(baselines_dir, f"{plugin_name}.py")
        package_path = os.path.join(baselines_dir, plugin_name, "__init__.py")
        if not os.path.exists(module_path) and not os.path.exists(package_path):
            print(f"  WARNING: No module found for plugin '{plugin_name}' (expected {module_path})")
            continue
        mod = importlib.import_module(f"baselines.{plugin_name}")
        # Find the BaselinePlugin subclass in the module
        plugin_class = None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if (isinstance(attr, type) and issubclass(attr, BaselinePlugin)
                    and attr is not BaselinePlugin and attr.name == plugin_name):
                plugin_class = attr
                break
        if plugin_class is None:
            print(f"  WARNING: No BaselinePlugin subclass with name='{plugin_name}' in {module_path}")
            continue
        plugins[plugin_name] = plugin_class

    return plugins


def topological_sort(cfg, plugin_names):
    """Sort plugin names respecting depends_on order."""
    baselines = cfg.get("baselines", {})
    # Build adjacency
    graph = {name: [] for name in plugin_names}
    in_degree = {name: 0 for name in plugin_names}
    name_set = set(plugin_names)

    for name in plugin_names:
        deps = baselines[name].get("depends_on", [])
        for dep in deps:
            if dep in name_set:
                graph[dep].append(name)
                in_degree[name] += 1

    # Kahn's algorithm
    queue = [n for n in plugin_names if in_degree[n] == 0]
    # Maintain original order for ties
    queue.sort(key=lambda n: plugin_names.index(n))
    result = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in graph[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
        queue.sort(key=lambda n: plugin_names.index(n))

    if len(result) != len(plugin_names):
        missing = set(plugin_names) - set(result)
        raise RuntimeError(f"Circular dependency detected involving: {missing}")

    return result


def run_data_health_check(cfg):
    """Run data health check to verify source data is fresh. Returns True if OK."""
    try:
        import data_health
    except ImportError:
        print("  WARNING: Could not import data_health module, skipping health check")
        return True

    # Build a minimal config compatible with data_health expectations
    health_config_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data", "health", "config.yaml"
    )
    if not os.path.exists(health_config_path):
        print(f"  WARNING: Health config not found at {health_config_path}, skipping health check")
        return True

    health_cfg = data_health.load_config(health_config_path)

    # Check staleness of each source
    stale_sources = []
    for source in health_cfg.get("sources", []):
        dr = data_health.check_date_range(health_cfg, source)
        if dr.get("stale"):
            stale_sources.append(source["name"])

    if stale_sources:
        print(f"  STALE data detected in: {', '.join(stale_sources)}")
        return False

    return True


def main():
    parser = argparse.ArgumentParser(description="Baseline computation orchestrator")
    parser.add_argument("--config", required=True, help="Path to baseline_config.yaml")
    parser.add_argument("--only", default="", help="Comma-separated list of plugin names to run")
    parser.add_argument("--symbols", default="", help="Comma-separated ISINs to process")
    parser.add_argument("--dry-run", action="store_true", help="Validate config and print plan only")
    args = parser.parse_args()

    cfg = load_config(args.config)

    # Inject symbols filter into config so plugins can access it
    if args.symbols:
        cfg["_symbols_filter"] = [s.strip() for s in args.symbols.split(",") if s.strip()]

    print("=" * 60)
    print("  BASELINE COMPUTATION")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # Discover plugins
    print("\nDiscovering plugins...")
    plugin_classes = discover_plugins(cfg)
    print(f"  Found {len(plugin_classes)} plugin module(s): {', '.join(sorted(plugin_classes))}")

    # Determine which plugins to run
    all_plugin_names = list(cfg.get("baselines", {}).keys())
    if args.only:
        requested = [s.strip() for s in args.only.split(",") if s.strip()]
        # Include dependencies automatically
        baselines = cfg["baselines"]
        to_run = set(requested)
        changed = True
        while changed:
            changed = False
            for name in list(to_run):
                for dep in baselines.get(name, {}).get("depends_on", []):
                    if dep not in to_run:
                        to_run.add(dep)
                        changed = True
        run_names = [n for n in all_plugin_names if n in to_run]
    else:
        run_names = all_plugin_names

    # Filter to enabled
    enabled_names = [n for n in run_names if cfg["baselines"][n].get("enabled", True)]

    # Topological sort
    sorted_names = topological_sort(cfg, enabled_names)

    # Filter to plugins that have module implementations
    executable = [n for n in sorted_names if n in plugin_classes]
    skipped = [n for n in sorted_names if n not in plugin_classes]

    # Validate that plugin class dependencies match config depends_on
    for name in executable:
        cls = plugin_classes[name]
        class_deps = sorted(cls.dependencies)
        config_deps = sorted(cfg["baselines"][name].get("depends_on", []))
        if class_deps != config_deps:
            print(f"  WARNING: {name} dependency mismatch — "
                  f"class={class_deps}, config={config_deps}")

    print(f"\nExecution plan ({len(executable)} plugins):")
    for i, name in enumerate(executable, 1):
        deps = cfg["baselines"][name].get("depends_on", [])
        dep_str = f" (after {', '.join(deps)})" if deps else ""
        print(f"  [{i}/{len(executable)}] {name}{dep_str}")
    if skipped:
        print(f"\n  Skipped (no module): {', '.join(skipped)}")
    if args.symbols:
        print(f"\n  Symbol filter: {args.symbols}")

    if args.dry_run:
        print("\n  DRY RUN — validating configs...")
        errors = []
        for name in executable:
            plugin = plugin_classes[name](cfg)
            try:
                plugin.validate_config()
                print(f"    {name}: OK")
            except Exception as e:
                print(f"    {name}: FAIL — {e}")
                errors.append(name)
        status = "PASSED" if not errors else f"FAILED ({len(errors)} error(s))"
        print(f"\n  Validation: {status}")
        print("=" * 60)
        return

    # Data health check
    print("\nRunning data health check...")
    health_ok = run_data_health_check(cfg)
    if not health_ok:
        print("  ABORTING: Source data is stale. Fix data pipeline first.")
        sys.exit(1)
    print("  Data health: OK")

    # Execute plugins
    print(f"\nRunning {len(executable)} plugins...\n")
    total_rows = 0
    total_start = time.time()
    results_summary = []

    for i, name in enumerate(executable, 1):
        plugin = plugin_classes[name](cfg)
        t0 = time.time()
        try:
            plugin.validate_config()
            results = plugin.compute()
            row_count = plugin.store(results)
            elapsed = time.time() - t0
            total_rows += row_count
            print(f"  [{i}/{len(executable)}] {name}... {row_count:,} rows ({elapsed:.1f}s)")
            results_summary.append({"plugin": name, "rows": row_count, "time": elapsed, "status": "OK"})
        except Exception as e:
            elapsed = time.time() - t0
            print(f"  [{i}/{len(executable)}] {name}... ERROR ({elapsed:.1f}s): {e}")
            results_summary.append({"plugin": name, "rows": 0, "time": elapsed, "status": f"ERROR: {e}"})

    total_elapsed = time.time() - total_start
    print(f"\n{'='*60}")
    print(f"  DONE: {total_rows:,} total rows in {total_elapsed:.1f}s")
    ok_count = sum(1 for r in results_summary if r["status"] == "OK")
    err_count = len(results_summary) - ok_count
    print(f"  Plugins: {ok_count} succeeded, {err_count} failed")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
