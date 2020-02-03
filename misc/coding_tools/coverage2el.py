from coverage import coverage, summary, misc

class ElispReporter(summary.SummaryReporter):
    def report(self, morfs=None):
        self.find_code_units(morfs)
        out = open(".coverage.el", "w")
        out.write("""
;; This is an elisp-readable form of the coverage data. It defines a
;; single top-level hash table in which the key is an asolute pathname, and
;; the value is a three-element list. The first element of this list is a
;; list of line numbers that represent actual code statements. The second is
;; a list of line numbers for lines which got used during the unit test. The
;; third is a list of line numbers for code lines that were not covered
;; (since 'code' and 'covered' start as sets, this last list is equal to
;; 'code - covered').

    """)
        out.write("(let ((results (make-hash-table :test 'equal)))\n")
        for cu in self.code_units:
            f = cu.filename
            try:
                (fn, executable, missing, mf) = self.coverage.analysis(cu)
            except misc.NoSource:
                continue
            code_linenumbers = executable
            uncovered_code = missing
            covered_linenumbers = sorted(set(executable) - set(missing))
            out.write(" (puthash \"%s\" '((%s) (%s) (%s)) results)\n"
                      % (f,
                         " ".join([str(ln) for ln in sorted(code_linenumbers)]),
                         " ".join([str(ln) for ln in sorted(covered_linenumbers)]),
                         " ".join([str(ln) for ln in sorted(uncovered_code)]),
                         ))
        out.write(" results)\n")
        out.close()

def main():
    c = coverage() # defaults to data_file=.coverage
    c.load()
    c._harvest_data()
    c.config.from_args(include="src/*")
    ElispReporter(c, c.config).report()

if __name__ == '__main__':
    main()


