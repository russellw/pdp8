# PDP-8 with hindsight

A PDP-8 emulator used as a sandbox for exploring **variations on the
architecture** — changes that could have been made in the 1960s, with the
benefit of hindsight, to improve code density or performance *without*
significantly adding to the hardware cost of the machine as it was actually
built.

The PDP-8 is a 12-bit minicomputer DEC sold from 1965, mostly for laboratory
and instrumentation work. This project emulates the **base CPU only** (8
memory-reference instructions, the microcoded OPR operate groups, program
interrupts, a teletype). The optional Extended Arithmetic Element (EAE — the
hardware multiply/divide unit) is deliberately omitted; foregoing it is what
frees the encoding space the experiments below spend.

The guiding workload is the kind of thing the machine was actually sold to
do — fixed-point arithmetic — for which a software multiply routine is a good
proxy. The aim is faster bread-and-butter code, not computer-science
conveniences like recursion.

## Layout

| Path | What it is |
|---|---|
| `original/` | The **pristine base machine**, preserved untouched. The reference every experiment is measured against. |
| `pdp8.py` | The **working variant** — the base machine plus the changes below. |
| `test_pdp8.py` | Test suite for the variant (`python3 test_pdp8.py`, no deps). |
| `measure.py` | A/B harness: builds the same demo on both `original/` and `pdp8.py` and reports code density and memory cycles. |

Run everything:

```sh
python3 test_pdp8.py     # 20/20 should pass
python3 pdp8.py          # runs the HELLO and multiply/print demos
python3 measure.py       # variant vs. base, side by side
```

## How things are measured

Two metrics, gathered identically for both machines so they compare directly:

- **Program words** — distinct memory locations an assembled demo occupies
  (instructions + data + constants). Static code density.
- **Memory cycles** — real PDP-8 execution time. The machine is memory-bound,
  so each instruction costs as many cycles as the words it touches: a fetch
  plus an execute cycle for a memory-reference op (one more if it defers
  through an indirect pointer), one cycle for a `JMP` or operate. A
  read-modify-write op (`ISZ`, and the new in-place shift) modifies its word
  during the rewrite half of the single execute cycle the destructive-read
  core performs anyway — so it is **two** cycles, not three. Counting raw
  memory accesses would miss that and understate a shift, so `measure.py`
  models per-instruction cost explicitly.

> Note: the emulator's own `cpu.cycles` counter counts *instructions*, which
> is a different (coarser) number. Performance claims here use the memory-cycle
> model in `measure.py`.

## The encoding budget

All eight primary opcodes are spoken for (six memory-reference, IOT, OPR), so
new instructions can't come from a spare opcode. They come from the **OPR
group-3 encoding space**, which the EAE used and which is now free. That space
is `op = 7`, bit `0o0400` set, bit `0o0001` set, leaving **7 free bits =
128 encodings** to allocate. You can spend them on one instruction with a
7-bit operand field, or two with 6-bit fields, etc. — but only that one
budget. The experiments below compete for it.

A *memory-reference* instruction wants 9 bits of addressing (indirect + page +
7-bit offset); only 7 are available, so any new memory op in this space is
necessarily addressing-limited (e.g. page-zero only).

## Changes so far

### 1. Removed redundant CLAs in the multiply loop (software, no ISA change)

`DCA` clears the accumulator as a side effect, so after every store a bare
`TAD` already loads into a clean AC — the `CLA; TAD` pairs in the original
multiply were defensive habit, not necessity. Every path through the loop
reaches its loads with AC already zero, so the internal `CLA`s were deleted
outright. **~17% faster on the multiply, no hardware involved.** A reminder
that some apparent ISA gaps are really just code smells.

### 2. In-place memory shift — `SHR` / `SHL` (committed; spends the group-3 slot)

The multiply's bottleneck was shifting its two operands. The PDP-8 can shift
only the accumulator, so each shift was `TAD` / rotate / `DCA` — 5 cycles, of
which 4 are just hauling the operand into AC and back. With the operand spilled
to memory every iteration, that dominated the loop.

But core memory is **destructive-read**: every access already reads a word and
rewrites it (this is exactly why `ISZ` can increment in place for the same
cost as a plain `TAD`). A shift is the same shape of operation. So group-3 now
decodes as an **in-place memory shift** that routes that rewrite through the
shifter the machine already has for `RAR`/`RAL`:

```
encoding (group 3):  0o7401 | direction | (addr << 1)
  bit 7 (0o0200)  = direction   (0 = right / SHR, 1 = left / SHL)
  bits 1-6        = 6-bit page-zero address (0-63; park hot scratch there)
  semantics       = logical shift, zero fill, bit shifted out -> link,
                    AC untouched; one 2-cycle read-modify-write (like ISZ)
```

`shr(addr)` / `shl(addr)` in `pdp8.py` encode the words. In the multiply, the
two five-cycle shift sequences collapse to single `SHR MPLR` / `SHL MCAND`
instructions, the multiplier's low bit drops straight into the link for the
add test, and the accumulator stops being a turnstile for every shift.

**Hardware cost:** modest, and plausibly "cheap at the time" — the shifter and
the read-modify-write cycle both already exist; what's added is a path from the
memory buffer through the shifter and a link-capture. Far cheaper than the EAE
being foregone.

### Explored and rejected: load-immediate (`TADI`) and load-from-memory

Both were considered for the single group-3 slot before the shift won it:

- **`TADI`** (add a signed 7-bit immediate, no data word) was prototyped and
  measured: a real but small win (~3–4% on the demos), since it only helps
  constant loads, and the dominant loop operands are variables, not constants.
- **Load-from-memory (`LDA`)** looked attractive (`CLA; TAD` is everywhere) but
  is weak here: the `DCA`-leaves-zero idiom already makes most loads free via
  bare `TAD`, and a group-3 `LDA` would be addressing-crippled (7 bits, not 9).

The shift targets the actual measured bottleneck and generalizes to the
fixed-point scaling and normalization that fill real lab code, so it took the
slot. The reasoning is preserved here because the *decision*, not just the
result, is the point of the project.

## Results (variant vs. base machine)

| Demo | Words | Memory cycles |
|---|---|---|
| `build_multiply` | 36 → **28** (−22%) | **~−40%** |
| `build_multiply_print` | 98 → **90** (−8%) | −16% to −23% |

Caveats on attribution:

- The ~40% on the pure multiply is **cumulative** — variant vs. pristine base —
  so it bundles the redundant-`CLA` cleanup (change 1) with the memory shift
  (change 2). The shift instruction itself is the larger, ~33% piece.
- `build_multiply_print` speeds up only because its embedded multiply got
  faster; its decimal-conversion code is subtract-and-compare, not shift-bound,
  and is unchanged. Its percentage shrinks as the printed number grows and more
  time goes to the (unchanged) print portion.

## Open threads

- **Single-accumulator spilling** still shows up outside the shift path — every
  intermediate has to go through the one AC. This is the next structural cost
  to look at.
- The 6-bit shift address reaches only page-zero 0–63; fine for hot scratch,
  but a constraint worth revisiting if the slot's allocation is ever rethought.
