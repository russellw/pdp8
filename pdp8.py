"""A PDP-8 emulator.

The PDP-8 is a 12-bit minicomputer made by Digital Equipment Corporation in
the 1960s. This emulates the base CPU: the 8 basic instructions, the OPR
microcoded operate instructions (groups 1 and 2), program-controlled
interrupts, and a teletype-style console device on IOT device 03/04.

The optional Extended Arithmetic Element (EAE) -- the MQ register and the
group-3 operate / multiply-divide instructions -- is deliberately omitted;
this is the minimal base machine.

All values are 12-bit unless noted. The link (L) is a single bit that sits
above the accumulator and catches carries.

Numbers in PDP-8 documentation are octal; this code follows that convention
(Python `0o...` literals) so addresses match the original listings.
"""

MASK = 0o7777          # 12-bit word mask
SIGN = 0o4000          # sign bit of a 12-bit word
MEM_WORDS = 0o10000    # 4096 words = one memory field
PAGE = 0o0200          # 128 words per page
ADDR = 0o0177          # 7-bit in-page offset


class PDP8:
    def __init__(self, memory_fields=1):
        self.mem = [0] * (MEM_WORDS * memory_fields)
        self.ac = 0          # accumulator (12 bits)
        self.l = 0           # link (1 bit)
        self.pc = 0o0200     # program counter; programs conventionally start at 0200
        self.sr = 0          # console switch register
        self.running = False
        self.halted = False
        # Interrupt state.
        self.ion = False     # interrupts enabled
        self.ion_delay = 0   # ION takes effect after the *next* instruction
        self.interrupt_req = False
        # Teletype device (03 = keyboard, 04 = printer).
        self.tty_in = []     # queued input characters (ints)
        self.tty_out = []    # captured output characters (ints)
        self.kbd_flag = False
        self.prt_flag = True  # printer ready to accept a character
        self.cycles = 0

    # --- memory helpers -------------------------------------------------
    def load(self, addr, value):
        self.mem[addr & (len(self.mem) - 1)] = value & MASK

    def fetch(self, addr):
        return self.mem[addr % len(self.mem)] & MASK

    def store(self, addr, value):
        self.mem[addr % len(self.mem)] = value & MASK

    def load_program(self, words, start=0o0200):
        """Load a sequence of 12-bit words into memory starting at `start`."""
        for i, w in enumerate(words):
            self.store(start + i, w)
        self.pc = start

    # --- effective address ----------------------------------------------
    def effective_address(self, instr):
        """Compute the effective address for a memory-reference instruction."""
        indirect = instr & 0o0400
        current_page = instr & 0o0200
        offset = instr & ADDR
        if current_page:
            addr = (self.pc - 1) & 0o7600 | offset  # PC already advanced past instr
        else:
            addr = offset  # page zero
        if indirect:
            # Auto-index: indirect reference through 0010-0017 pre-increments.
            if 0o0010 <= addr <= 0o0017:
                self.store(addr, (self.fetch(addr) + 1) & MASK)
            addr = self.fetch(addr)
        return addr & MASK

    # --- the operate (OPR) instruction ----------------------------------
    def operate(self, instr):
        if not (instr & 0o0400):
            # Group 1: clears, complements, rotates, increment.
            if instr & 0o0200:            # CLA
                self.ac = 0
            if instr & 0o0100:            # CLL
                self.l = 0
            if instr & 0o0040:            # CMA
                self.ac ^= MASK
            if instr & 0o0020:            # CML
                self.l ^= 1
            if instr & 0o0001:            # IAC
                self.ac += 1
                if self.ac & 0o10000:
                    self.l ^= 1
                self.ac &= MASK
            rotate = instr & 0o0016
            if rotate & 0o0010:           # RAR / RTR (rotate right)
                self._rar()
                if instr & 0o0002:
                    self._rar()
            elif rotate & 0o0004:         # RAL / RTL (rotate left)
                self._ral()
                if instr & 0o0002:
                    self._ral()
        elif not (instr & 0o0001):
            # Group 2: skips, OSR, HLT, CLA.
            skip = False
            sma = instr & 0o0100          # skip on minus AC
            sza = instr & 0o0040          # skip on zero AC
            snl = instr & 0o0020          # skip on non-zero link
            reverse = instr & 0o0010      # invert the sense (AND the conditions)
            if reverse:
                # SPA / SNA / SZL: skip if all the *negated* conditions hold.
                skip = True
                if sma and (self.ac & SIGN):
                    skip = False
                if sza and self.ac == 0:
                    skip = False
                if snl and self.l:
                    skip = False
            else:
                if sma and (self.ac & SIGN):
                    skip = True
                if sza and self.ac == 0:
                    skip = True
                if snl and self.l:
                    skip = True
            if skip:
                self.pc = (self.pc + 1) & MASK
            if instr & 0o0200:            # CLA
                self.ac = 0
            if instr & 0o0004:            # OSR (or switch register into AC)
                self.ac |= self.sr
            if instr & 0o0002:            # HLT
                self.halted = True
                self.running = False
        else:
            # Group 3 encodings belong to the optional EAE (MQ register,
            # multiply/divide). On the base machine they are unimplemented and
            # behave as no-ops.
            pass

    def _rar(self):
        new_l = self.ac & 1
        self.ac = (self.ac >> 1) | (self.l << 11)
        self.l = new_l

    def _ral(self):
        new_l = (self.ac >> 11) & 1
        self.ac = ((self.ac << 1) | self.l) & MASK
        self.l = new_l

    # --- IOT (input/output) ---------------------------------------------
    def iot(self, instr):
        device = (instr >> 3) & 0o077
        func = instr & 0o7
        if device == 0o00:
            # Processor interrupt control.
            if func == 0o1:    # ION
                self.ion_delay = 1
            elif func == 0o2:  # IOF
                self.ion = False
        elif device == 0o03:
            # Keyboard / reader.
            if func & 0o1:     # KSF: skip if keyboard flag set
                if self.kbd_flag:
                    self.pc = (self.pc + 1) & MASK
            if func & 0o2:     # KCC: clear flag and AC
                self.kbd_flag = False
                self.ac = 0
            if func & 0o4:     # KRS: read keyboard buffer into AC
                if self.tty_in:
                    self.ac |= self.tty_in.pop(0) & 0o377
        elif device == 0o04:
            # Teleprinter / punch.
            if func & 0o1:     # TSF: skip if printer flag set
                if self.prt_flag:
                    self.pc = (self.pc + 1) & MASK
            if func & 0o2:     # TCF: clear printer flag
                self.prt_flag = False
            if func & 0o4:     # TPC: print AC (low 8 bits)
                self.tty_out.append(self.ac & 0o377)
                self.prt_flag = True
        # Unknown devices are silently ignored, as real hardware would be.

    # --- one instruction -------------------------------------------------
    def step(self):
        # Service a pending interrupt before fetching the next instruction.
        if self.ion and self.interrupt_req:
            self.ion = False
            self.interrupt_req = False
            self.store(0, self.pc)   # save PC in location 0
            self.pc = 1              # vector to location 1
            return

        instr = self.fetch(self.pc)
        self.pc = (self.pc + 1) & MASK
        self.cycles += 1
        op = instr >> 9

        if op == 0:                      # AND
            self.ac &= self.fetch(self.effective_address(instr))
        elif op == 1:                    # TAD (two's-complement add)
            total = self.ac + self.fetch(self.effective_address(instr))
            if total & 0o10000:
                self.l ^= 1
            self.ac = total & MASK
        elif op == 2:                    # ISZ (increment and skip if zero)
            addr = self.effective_address(instr)
            val = (self.fetch(addr) + 1) & MASK
            self.store(addr, val)
            if val == 0:
                self.pc = (self.pc + 1) & MASK
        elif op == 3:                    # DCA (deposit and clear AC)
            self.store(self.effective_address(instr), self.ac)
            self.ac = 0
        elif op == 4:                    # JMS (jump to subroutine)
            addr = self.effective_address(instr)
            self.store(addr, self.pc)
            self.pc = (addr + 1) & MASK
        elif op == 5:                    # JMP
            self.pc = self.effective_address(instr)
        elif op == 6:                    # IOT
            self.iot(instr)
        else:                            # OPR
            self.operate(instr)

        # Deferred enabling of interrupts (ION takes effect after one instr).
        if self.ion_delay:
            self.ion_delay -= 1
            if self.ion_delay == 0:
                self.ion = True

    def run(self, max_cycles=1_000_000):
        """Run until HLT or until max_cycles instructions execute."""
        self.running = True
        self.halted = False
        count = 0
        while self.running and count < max_cycles:
            self.step()
            count += 1
        return count

    # --- console output --------------------------------------------------
    def output_text(self):
        return "".join(chr(c) for c in self.tty_out)


# ----------------------------------------------------------------------
# Demo: a tiny program that prints "HELLO" to the teletype, then halts.
# ----------------------------------------------------------------------
def _demo():
    cpu = PDP8()
    msg = "HELLO, WORLD!\n"
    PTR = 0o0010      # autoindex register 10
    TABLE = 0o0300    # the message string lives here, 0-terminated

    # Hand-assembled program. The main loop reads characters through autoindex
    # register 10 (which pre-increments on each indirect reference) and calls
    # the PRINT subroutine until it hits the 0 terminator.
    asm = {
        0o0200: 0o1410,   # TAD I 10   ; AC = next char (autoindex via reg 10)
        0o0201: 0o7450,   # SNA        ; skip next if AC != 0
        0o0202: 0o7402,   # HLT        ; terminator reached -> stop
        0o0203: 0o4210,   # JMS 0210   ; print the character in AC
        0o0204: 0o5200,   # JMP 0200   ; back to top of loop
        # PRINT subroutine ----------------------------------------------
        0o0210: 0o0000,   #            ; return-address slot (filled by JMS)
        0o0211: 0o6046,   # TLS        ; load printer buffer from AC, start print
        0o0212: 0o6041,   # TSF        ; skip when printer ready
        0o0213: 0o5212,   # JMP .-1    ; else wait
        0o0214: 0o7200,   # CLA        ; clear AC
        0o0215: 0o5610,   # JMP I 0210 ; return to caller
    }
    for addr, word in asm.items():
        cpu.store(addr, word)

    # Pointer starts at TABLE-1 so the first pre-increment lands on TABLE.
    cpu.store(PTR, TABLE - 1)
    for i, ch in enumerate(msg):
        cpu.store(TABLE + i, ord(ch))
    cpu.store(TABLE + len(msg), 0)  # terminator

    cpu.pc = 0o0200
    cpu.run()
    print("Output:", repr(cpu.output_text()))
    print("Cycles:", cpu.cycles)


if __name__ == "__main__":
    _demo()
