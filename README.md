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
| `dump.cs` | Il2CppDumper output (detected by `// TypeDefIndex:`): field offsets and method RVAs |
| `Assertions.inl`, SDK `.hpp` | Dumper-7 (Unreal) layout `static_assert`s: member offsets and struct sizes |
| directory | dumper output folder; one entry per file stem, JSON preferred over source |

## Usage

```
python -m offset_diff OLD NEW [-f text|md|json|html] [-o FILE] [-g]
                              [-i PAT] [-x PAT] [--min-delta N] [--fail-on-change]
                              [--repo DIR [-p PATH]]
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
- `-g` / `--group`: add a layout-shift summary (see below).
- `--fail-on-change`: exit code 1 if anything differs, for CI and watchers.
- `-f md`: markdown table, paste it straight into a patch-day issue.
- `-f html -o report.html`: one self-contained page (no network, light and dark) with a
  live filter box over every row. Attach it to a release or open it locally.

## Git history

Dumper repos commit their output on every patch. Diff two commits, tags or branches without
checking anything out:

```
python -m offset_diff --repo path/to/cs2-dumper HEAD~1:output/offsets.json HEAD:output/offsets.json
python -m offset_diff --repo path/to/cs2-dumper v1.2:output v1.3:output -g     # a whole folder
python -m offset_diff --repo path/to/cs2-dumper HEAD~5 HEAD -p output          # bare revisions + --path
```

OLD and NEW are git's `REV:PATH`. `REV` is anything git resolves: a hash, tag, branch,
`HEAD~3`. `PATH` is a file or a folder of dumper output, relative to the repo root (`REV:`
alone is the root). Every other flag works as before. Needs `git` on PATH.

## Watching a dumper repo

`watch` follows a local clone of a dumper repo and prints a diff for every new commit that
moved an offset:

```
python -m offset_diff watch --repo path/to/cs2-dumper -p output/offsets.json --state last.txt
```

```
commit 9f3a1c20  Update 2026-11-14 build 20481337
--- 4be07d51:output/offsets.json
+++ 9f3a1c20:output/offsets.json
2 changed, 0 added, 0 removed, 0 renamed?, 410 unchanged
...
```

- Each poll runs `git fetch`, then reads every new commit that touched `-p PATH` (oldest
  first) from the object store and compares it with the dump after the previous commit. The
  working tree is never touched. `--ref origin/main` follows something other than the upstream, and
  `--no-fetch` skips the fetch.
- A commit that touched the file but moved no offset (reformatting, a timestamp) prints nothing.
  A commit whose dump doesn't parse is skipped with a warning, and the next one is compared
  with the last good state.
- The first run starts from now (`--since REV` starts from `REV` instead). `--state FILE`
  remembers the last commit seen, so a restart resumes where it stopped, and wins over `--since`.
- `--interval 300` seconds between checks. `--once` checks once and exits with status 1 if any
  offsets moved, 0 if not, 2 on error: put it in cron or Task Scheduler instead of leaving a
  loop running.
- `-f text|md|json`: json is one object per line, with `commit` and `subject` added.
  `--out-dir DIR` also writes each report to `DIR/<commit>.<ext>`.
- `-i`, `-x`, `--only`, `--min-delta` and `-g` work as in the main command.
- `--exec CMD [ARGS...]` runs a command for every update, with the report on stdin. It must be
  the **last** option, since everything after it is the command. No shell is involved: the
  command is an argument list, and what the commit says reaches it only through environment
  variables, so a hostile commit message can't inject anything.

  | Variable | Value |
  | --- | --- |
  | `OFFSET_DIFF_COMMIT`, `OFFSET_DIFF_BASE` | full hashes of the new commit and the one it was compared with |
  | `OFFSET_DIFF_SUBJECT` | the commit's first line, verbatim |
  | `OFFSET_DIFF_PATH`, `OFFSET_DIFF_FORMAT` | the watched path, and `text` / `md` / `json` |
  | `OFFSET_DIFF_CHANGED`, `_ADDED`, `_REMOVED`, `_RENAMED` | counts |

  A command that fails to start, exits non-zero or runs past 60 s only costs a warning; the
  watcher keeps going. For example, `... --exec python notify.py` where `notify.py` posts
  `os.environ["OFFSET_DIFF_SUBJECT"]` and the counts to a chat webhook.

## History of one offset

`history` answers "when did this move, and by how much?" It walks the commits that touched a
file or folder in a dumper repo, newest first like `git log`, and prints what each one changed
compared with the commit before it:

```
$ python -m offset_diff history --repo path/to/cs2-dumper -p output/offsets.json -n 12 -i dwViewMatrix
6fe30c6d  2026-11-12  Game update (14197)
    ~ client.dll.dwViewMatrix  0x2570250 -> 0x2559640  (-0x16C10)

32d471a6  2026-11-09  Game update (14196)
    ~ client.dll.dwViewMatrix  0x256F1D0 -> 0x2570250  (+0x1080)
...
```

- `-n N` looks at the last N commits that touched `-p PATH` (default 20, `0` is all of them).
  When that reaches the first commit ever, it shows what that commit introduced; otherwise the
  oldest commit in range is only the baseline the next one is compared with.
- Commits that touched the file but moved none of the selected offsets (reformatting, other
  keys) are left out, so `-i dwViewMatrix` is a timeline of that one offset. `-i`, `-x`,
  `--only` and `--min-delta` work as in the main command; `--ref` starts somewhere other than `HEAD`.
- A commit whose dump doesn't parse is skipped with a warning, and the next one is compared
  with the last good state. `-f json` prints one object per commit.
- Each commit's dump is read straight from git, never checked out. On a partial clone
  (`--filter=blob:none`) every blob is fetched on demand, so it is slower there.

## Unity / Il2CppDumper

Diff two `dump.cs` files to see how a game update moved class layouts and code.

```
python -m offset_diff build_a/dump.cs build_b/dump.cs -i Player          # everything on Player types
python -m offset_diff build_a/dump.cs build_b/dump.cs --only fields      # drop method RVAs
python -m offset_diff build_a/dump.cs build_b/dump.cs --only methods -i Player
```

Keys look like `Game.Player.health` (field offset) and
`Game.Player.TakeDamage(int, ref Vector3)` (method RVA). Parameter names are dropped, so
renaming an argument is not a new method. Fields and methods are separate scopes for
rename detection, because method order shifts between builds far more than field order.
Parsing runs at roughly 16 MB/s (a 17.5 MB synthetic dump takes about 1 s).

## Layout shifts

When a class grows, every field after the new member moves by the same amount, and a
200-line CHANGED list hides that. `--group` adds one line per run:

```
$ python -m offset_diff a.json b.json -g
LAYOUT SHIFTS (neighbouring fields that moved by the same amount)
  C_BasePlayer  +0x8  m_iHealth .. m_iArmor  (14 fields, from 0x3E8 -> 0x3F0)
```

A run is two or more neighbouring fields of one class, in old-layout order, that all moved
by the same non-zero amount. An unchanged field ends a run, so two separate shifts are
never merged. Method RVAs are left out because they move independently. It works in every
`-f` format (`shifts` in JSON); without `-g` the output is unchanged.

## Unreal / Dumper-7

Diff two Dumper-7 SDKs to see how an Unreal game update moved its classes. Point it at the
`Assertions.inl` Dumper-7 writes (or at the SDK folder):

```
python -m offset_diff build_a/CppSDK/Assertions.inl build_b/CppSDK/Assertions.inl -g
python -m offset_diff build_a/CppSDK build_b/CppSDK -i AActor
```

Dumper-7 records what it measured as `static_assert(offsetof(UObject, Class) == 0x000010, ...)`
and `static_assert(sizeof(UObject) == 0x000028, ...)`. Those become keys like
`UObject.Class` (member offset) and `UObject.sizeof` (struct size), so everything else works
as usual: `-g` turns "the class grew" into one line (the size itself is not counted as a
member), renames are found by position, `-i`/`-x` and `-f html` apply. `alignof` lines are
skipped. Both the default macro form (`#define DUMPER7_ASSERTS_<Class> \`) and inline
assertions are read, and `namespace Offsets { GObjects, ProcessEvent, ... }` in `Basic.hpp`
comes along as `Offsets.GObjects`.

- Keys name their class and not the file it sits in, so a class moving between `Engine_classes.hpp`
  and another file between builds is not reported as removed and added.
- The format was taken from Dumper-7's generator source (`CppGenerator.cpp`), not from a
  real game's SDK: I had none to run it on. A large synthetic file in the same format (33 MB,
  270,000 keys) parses in about a second, and a 284,000-key diff with 6,000 changed classes
  takes about one second.
- Not covered: the `.usmap` mappings file and Dumper-7's Dumpspace JSON output.

## Rename detection

A removed key and an added key are paired as a possible rename in two ways, strongest first:

1. **same value**: both sides share a unique non-zero value. Within one class or module that
   is enough; across two different ones the leaf names must also be alike (60%+), since small
   offsets repeat all over a struct dump. `UFoo.Health -> UBar.Health` (a renamed class) still
   matches, `UWidget.OldName -> UNewThing.Value` (two unrelated members at `0x30`) does not.
2. **same position**: the value moved, but the key sits in the same slot between neighbours
   that exist in both dumps (same scope, same relative order). One key gone and one
   key new in a slot is a rename. A slot at the edge of a scope also needs similar names,
   and a scope whose neighbours were reordered is skipped.

```
C_BasePlayer.m_iOldArmor -> C_BasePlayer.m_iArmorValue  (0x3EC -> 0x3F4, same position, name 67%)
```

Candidates are ranked by leaf-name similarity. They are hints, not proof: verify before
you trust one.

## Tests

```
python -m unittest discover -s tests
```

## Roadmap

- [x] flag struct-size shifts per class (`--group`)
- [ ] schema-aware mode: read real struct sizes and field types from the dumper
- [ ] signature-aware mode: carry a pattern next to each offset, re-scan the new binary
- [x] diff two revisions of a git repo (`--repo`)
- [x] `history`: follow one offset (or the whole dump) across the commits of a dumper repo
- [x] `watch` command: poll a dumper repo, diff on every new commit
- [x] `watch --exec CMD` to notify when offsets move
- [x] rename candidates ranked by name similarity
- [x] rename detection by position when the value shifted (same slot between stable neighbours)
- [x] HTML report (`-f html`)
- [x] Il2CppDumper `dump.cs` parser
- [x] Dumper-7 SDK parser (`Assertions.inl` / header `static_assert`s)
- [ ] Dumper-7 `.usmap` mappings and Dumpspace JSON
- [x] `--only fields|methods` for il2cpp dumps
- [ ] PyPI release

## License

MIT
