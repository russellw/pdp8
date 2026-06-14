"""Compare an ISA variant against the preserved base machine in original/.

Two metrics, measured identically for both builds so the numbers are
comparable:

  * program words -- distinct memory locations written while assembling the
    demo. This is static code density (instructions + data + constants).

  * memory cycles -- the real PDP-8 execution time. The machine is
    memory-bound, so each instruction costs as many cycles as the words it
    touches: a fetch plus one execute cycle for a memory-reference op (an
    extra cycle if it defers through an indirect pointer), a single cycle for
    a JMP or an operate. Crucially, a read-modify-write op (ISZ, and this
    variant's in-place shift) modifies its word during the rewrite half of
    the one execute cycle the destructive-read core performs anyway -- so it
    is two cycles, not three. Counting raw fetch/store calls would miss that
    and understate a shift, hence the explicit per-instruction model below.

Word footprint is gathered by wrapping store at the class level; cycles by
decoding each instruction as it executes. No counters live in the emulator.
"""
import importlib.util


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def instr_cycles(instr):
    """Memory cycles for one executed instruction, on real PDP-8 timing."""
    op = instr >> 9
    if op <= 4:                          # AND TAD ISZ DCA JMS: fetch + execute
        return 3 if (instr & 0o0400) else 2   # +1 for an indirect defer
    if op == 5:                          # JMP
        return 2 if (instr & 0o0400) else 1
    if op == 6:                          # IOT
        return 2
    if (instr & 0o0400) and (instr & 0o0001):  # OPR group 3 = in-place shift
        return 2                         # read-modify-write, like ISZ
    return 1                             # OPR group 1/2


def measure(mod, builder, a, b):
    PDP8 = mod.PDP8
    real_store = PDP8.store

    # --- code density: which addresses does assembly touch? --------------
    touched = set()

    def recording_store(self, addr, val):
        touched.add(addr % len(self.mem))
        real_store(self, addr, val)

    PDP8.store = recording_store
    cpu = getattr(mod, builder)(a, b)
    PDP8.store = real_store
    words = len(touched)

    # --- performance: sum memory cycles over the executed instructions ---
    cycles = n = 0
    cpu.running, cpu.halted = True, False
    while cpu.running and n < 1_000_000:
        cycles += instr_cycles(cpu.fetch(cpu.pc))
        cpu.step()
        n += 1

    return words, cycles, cpu.output_text().strip()


def compare(builder, cases):
    orig = _load("original/pdp8.py", "orig_pdp8")
    new = _load("pdp8.py", "new_pdp8")

    print(f"== {builder} ==")
    print(f"{'a':>4} {'b':>4} | {'words':>13} | {'memory cycles':>22} | result")
    print("-" * 72)
    ow = nw = None
    for a, b in cases:
        ow, ocyc, ores = measure(orig, builder, a, b)
        nw, ncyc, nres = measure(new, builder, a, b)
        ok = "ok" if ores == nres else f"MISMATCH {ores!r} vs {nres!r}"
        pct = 100 * (ocyc - ncyc) / ocyc
        print(f"{a:>4} {b:>4} | {ow:>4} -> {nw:<5} "
              f"| {ocyc:>6} -> {ncyc:<6} ({pct:+4.0f}%) | {nres}  {ok}")
    print(f"\nstatic code density: {ow} -> {nw} words "
          f"({ow - nw} fewer, {100 * (ow - nw) / ow:.0f}% smaller)\n")


if __name__ == "__main__":
    compare("build_multiply", [(7, 6), (63, 63), (100, 100), (255, 255)])
    compare("build_multiply_print", [(7, 6), (25, 9), (63, 63), (100, 100)])
