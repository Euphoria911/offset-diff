# offset-diff

Diff offset dumps across game builds. Point it at two dumps and see what moved,
what vanished, and what was probably just renamed.

Zero dependencies. Python 3.10+.

## Formats

| Input | Notes |
| --- | --- |
| `.json` | any nesting; ints and `"0x.."` strings are kept |
| `.hpp` / `.h` | `constexpr` / `const` ints, keyed by enclosing `namespace` / `class` / `struct` |
| `.rs` | `pub const NAME: usize = 0x..;` inside `mod` blocks |
| `.cs` | `public const nint Name = 0x..;` inside classes |
| directory | dumper output folder; one entry per file stem, JSON preferred over source |

## Usage

```
python -m offset_diff OLD NEW [-f text|md|json] [-o FILE]
                              [-i PAT] [-x PAT] [--min-delta N] [--fail-on-change]
```

```
$ python -m offset_diff build_a/offsets.json build_b/offsets.json -i client
--- build_a/offsets.json
+++ build_b/offsets.json
2 changed, 1 added, 1 removed, 1 renamed?, 410 unchanged

CHANGED
  offsets.client.dll.dwViewMatrix       0x1E2C650 -> 0x1E31A10  (+0x53C0)
  offsets.client.dll.dwLocalPlayerPawn  0x1BE7DA0 -> 0x1BEA2D0  (+0x2530)
...
```

- `-i` / `-x`: include / exclude keys. Glob patterns, or a plain substring.
- `--min-delta 0x10`: hide shifts smaller than 16 bytes.
- `--fail-on-change`: exit code 1 if anything differs, for CI and watchers.
- `-f md`: markdown table, paste it straight into a patch-day issue.

## Tests

```
python -m unittest discover -s tests
```

## Roadmap

- [ ] schema-aware mode: group class fields under their class, flag struct-size shifts
- [ ] signature-aware mode: carry a pattern next to each offset, re-scan the new binary
- [ ] `watch` command: poll a dumper repo, diff on every new commit
- [ ] confidence score for rename detection (name similarity + neighbour offsets)
- [ ] HTML report
- [ ] Dumper-7 (`.usmap` / SDK header) and Il2CppDumper (`dump.cs`) parsers
- [ ] PyPI release

## License

MIT
