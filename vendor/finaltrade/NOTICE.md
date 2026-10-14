# Finaltrade reference source

Copied from the user-provided Laimiu-debug/final-trade working tree with explicit authorization to reproduce its screener and strategies. The baseline was captured unchanged. A-trade now maintains targeted fixes in this copy; source-manifest.json records the exact shipped bytes and the upstreamSha256 of each changed file. The external reference checkout is not modified. No account files, credentials, databases, market caches or runtime data are included.

The 2026-10-14 fixes restore the force-rhythm plugin and independent exit checks, reject unregistered implementations, isolate corrected cached results, require an eligible stable region for parameter recommendations, retain heatmap axis controls without a chart, and correct asynchronous screener completion counts. Legacy force-rhythm reports remain available with an explicit rerun warning.

The complete-grid update adds 49/121/441-point plane presets and strict coordinate parsing, raises individual axes to 64 values, evaluates the entire grid instead of a truncated prefix, and rejects products above the 2000-point task limit. Interval sampling remains available for higher-dimensional research. The emotion-cycle tool lives in the A-trade host and does not change strategy entry or exit signals.
