"""Tests for the PDP-8 emulator. Run with: python3 -m pytest test_pdp8.py
or just: python3 test_pdp8.py
"""
from pdp8 import PDP8, MASK, build_multiply, build_multiply_print


def run(words, start=0o0200, **state):
    cpu = PDP8()
    for k, v in state.items():
        setattr(cpu, k, v)
    cpu.load_program(words, start)
    cpu.run(max_cycles=10000)
    return cpu


def test_tad_basic():
    # TAD adds memory to AC.
    cpu = PDP8()
    cpu.store(0o0210, 5)
    cpu.load_program([0o1210, 0o7402], 0o0200)  # TAD 0210 ; HLT
    cpu.ac = 3
    cpu.run()
    assert cpu.ac == 8


def test_tad_link_carry():
    # Adding past 7777 toggles the link.
    cpu = PDP8()
    cpu.store(0o0210, 1)
    cpu.load_program([0o1210, 0o7402], 0o0200)
    cpu.ac = 0o7777
    cpu.l = 0
    cpu.run()
    assert cpu.ac == 0
    assert cpu.l == 1


def test_dca():
    cpu = PDP8()
    cpu.load_program([0o3210, 0o7402], 0o0200)  # DCA 0210 ; HLT
    cpu.ac = 0o1234
    cpu.run()
    assert cpu.fetch(0o0210) == 0o1234
    assert cpu.ac == 0  # DCA clears AC


def test_and():
    cpu = PDP8()
    cpu.store(0o0210, 0o0707)
    cpu.load_program([0o0210, 0o7402], 0o0200)  # AND 0210 ; HLT
    cpu.ac = 0o7070
    cpu.run()
    assert cpu.ac == 0o0000


def test_isz_skips_on_zero():
    cpu = PDP8()
    cpu.store(0o0210, 0o7777)  # increments to 0 -> skip
    # ISZ 0210 ; HLT(skipped) ; HLT
    cpu.load_program([0o2210, 0o7402, 0o7402], 0o0200)
    cpu.run()
    assert cpu.fetch(0o0210) == 0
    assert cpu.pc == 0o0203  # skipped the first HLT then ran the second


def test_jms_jmp():
    cpu = PDP8()
    # JMS 0210 ; HLT     subroutine at 0210 just returns
    cpu.load_program([0o4210, 0o7402], 0o0200)
    cpu.store(0o0210, 0)       # return slot
    cpu.store(0o0211, 0o5610)  # JMP I 0210 (return)
    cpu.run()
    assert cpu.fetch(0o0210) == 0o0201  # return address stored
    assert cpu.halted


def test_autoindex():
    cpu = PDP8()
    cpu.store(0o0010, 0o0210 - 1)  # autoindex reg, points before data
    cpu.store(0o0210, 0o0042)
    cpu.load_program([0o1410, 0o7402], 0o0200)  # TAD I 10 ; HLT
    cpu.run()
    assert cpu.fetch(0o0010) == 0o0210  # pre-incremented
    assert cpu.ac == 0o0042


def test_rotate_ral():
    cpu = PDP8()
    cpu.load_program([0o7004, 0o7402], 0o0200)  # RAL ; HLT
    cpu.ac = 0o4000
    cpu.l = 0
    cpu.run()
    # 0o4000 rotated left through link: bit11 -> link, AC<<1
    assert cpu.l == 1
    assert cpu.ac == 0


def test_rotate_rar():
    cpu = PDP8()
    cpu.load_program([0o7010, 0o7402], 0o0200)  # RAR ; HLT
    cpu.ac = 1
    cpu.l = 0
    cpu.run()
    assert cpu.l == 1
    assert cpu.ac == 0


def test_cma_iac_is_negate():
    # CMA IAC (= CIA) negates the accumulator.
    cpu = PDP8()
    cpu.load_program([0o7041, 0o7402], 0o0200)  # CMA IAC ; HLT
    cpu.ac = 5
    cpu.run()
    assert cpu.ac == (-5 & MASK)


def test_skip_sma():
    cpu = PDP8()
    cpu.load_program([0o7500, 0o7402, 0o7402], 0o0200)  # SMA ; HLT ; HLT
    cpu.ac = 0o4000  # negative
    cpu.run()
    assert cpu.pc == 0o0203  # skipped first HLT


def test_osr():
    cpu = PDP8()
    cpu.sr = 0o0077
    cpu.load_program([0o7404, 0o7402], 0o0200)  # OSR ; HLT
    cpu.ac = 0o7700
    cpu.run()
    assert cpu.ac == 0o7777


def test_teletype_output():
    cpu = PDP8()
    # CLA; TAD char; TLS; HLT  -> prints one character
    cpu.store(0o0210, ord('A'))
    cpu.load_program([0o7200, 0o1210, 0o6046, 0o7402], 0o0200)
    cpu.run()
    assert cpu.output_text() == 'A'


def test_software_multiply():
    # The shift-and-add subroutine, across edge cases and a result that
    # overflows 12 bits (100*100 = 10000 -> 1808 mod 4096).
    for a, b in [(0, 5), (5, 0), (1, 1), (7, 6), (12, 12),
                 (25, 9), (63, 63), (100, 100)]:
        cpu = build_multiply(a, b)
        cpu.run()
        assert cpu.fetch(0o0216) == (a * b) & MASK, (a, b)
        assert cpu.ac == 0  # AC cleared by DCA RESULT after the call


def test_multiply_print_decimal():
    # The product is converted to decimal in-machine and printed to the
    # teletype. Covers leading-zero suppression, a lone zero, and 4 digits.
    cases = [(0, 5), (7, 6), (12, 12), (25, 9), (63, 63),
             (50, 20), (100, 10), (100, 100)]  # 1000, 1000, 1808 (wrapped)
    for a, b in cases:
        cpu = build_multiply_print(a, b)
        cpu.run()
        assert cpu.output_text() == f"{(a * b) & MASK}\n", (a, b)


def test_interrupt():
    cpu = PDP8()
    # ION ; NOP ; NOP ; HLT   then raise interrupt -> vector to 1
    cpu.load_program([0o6001, 0o7000, 0o7000, 0o7402], 0o0200)
    cpu.store(0o0001, 0o7402)  # HLT at interrupt vector
    cpu.step()  # ION (effective after next instr)
    cpu.step()  # NOP -> interrupts now enabled
    cpu.interrupt_req = True
    cpu.step()  # interrupt taken: PC saved to 0, jump to 1
    assert cpu.fetch(0) != 0   # return address saved
    assert cpu.pc == 1


if __name__ == "__main__":
    import sys
    funcs = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in funcs:
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)
