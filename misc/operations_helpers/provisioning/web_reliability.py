
from nevow import rend, loaders, tags as T
from nevow.inevow import IRequest
import reliability # requires NumPy
import util

def get_arg(ctx_or_req, argname, default=None, multiple=False):
    """Extract an argument from either the query args (req.args) or the form
    body fields (req.fields). If multiple=False, this returns a single value
    (or the default, which defaults to None), and the query args take
    precedence. If multiple=True, this returns a tuple of arguments (possibly
    empty), starting with all those in the query args.
    """
    req = IRequest(ctx_or_req)
    results = []
    if argname in req.args:
        results.extend(req.args[argname])
    if req.fields and argname in req.fields:
        results.append(req.fields[argname].value)
    if multiple:
        return tuple(results)
    if results:
        return results[0]
    return default


DAY=24*60*60
MONTH=31*DAY
YEAR=365*DAY

def is_available():
    if reliability:
        return True
    return False

def yandm(seconds):
    return "%dy.%dm" % (int(seconds/YEAR), int( (seconds%YEAR)/MONTH))

class ReliabilityTool(rend.Page):
    addSlash = True
    docFactory = loaders.xmlfile(util.sibling("reliability.xhtml"))

    DEFAULT_PARAMETERS = [
        ("drive_lifetime", "8Y", "time",
         "Average drive lifetime"),
        ("k", 3, "int",
         "Minimum number of shares needed to recover the file"),
        ("R", 7, "int",
         "Repair threshold: repair will not occur until fewer than R shares "
         "are left"),
        ("N", 10, "int",
         "Total number of shares of the file generated"),
        ("delta", "1M", "time", "Amount of time between each simulation step"),
        ("check_period", "1M", "time",
         "How often to run the checker and repair if fewer than R shares"),
        ("report_period", "3M", "time",
         "Amount of time between result rows in this report"),
        ("report_span", "5Y", "time",
         "Total amount of time covered by this report"),
        ]

    def parse_time(self, s):
        if s.endswith("M"):
            return int(s[:-1]) * MONTH
        if s.endswith("Y"):
            return int(s[:-1]) * YEAR
        return int(s)

    def format_time(self, s):
        if s%YEAR == 0:
            return "%dY" % (s/YEAR)
        if s%MONTH == 0:
            return "%dM" % (s/MONTH)
        return "%d" % s

    def get_parameters(self, ctx):
        parameters = {}
        for (name,default,argtype,description) in self.DEFAULT_PARAMETERS:
            v = get_arg(ctx, name, default)
            if argtype == "time":
                value = self.parse_time(v)
            else:
                value = int(v)
            parameters[name] = value
        return parameters

    def renderHTTP(self, ctx):
        self.parameters = self.get_parameters(ctx)
        self.results = reliability.ReliabilityModel.run(**self.parameters)
        return rend.Page.renderHTTP(self, ctx)

    def make_input(self, name, old_value):
        return T.input(name=name, type="text", size="5",
                       value=self.format_time(old_value))

    def render_forms(self, ctx, data):
        f = T.form(action=".", method="get")
        table = []
        for (name,default_value,argtype,description) in self.DEFAULT_PARAMETERS:
            old_value = self.parameters[name]
            i = self.make_input(name, old_value)
            table.append(T.tr[T.td[name+":"], T.td[i], T.td[description]])
        go = T.input(type="submit", value="Recompute")
        return [T.h2["Simulation Parameters:"],
                f[T.table[table], go],
                ]

    def data_simulation_table(self, ctx, data):
        for row in self.results.samples:
            yield row

    def render_simulation_row(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = row
        ctx.fillSlots("t", yandm(when))
        ctx.fillSlots("P_repair", "%.6f" % P_repaired_last_check_period)
        ctx.fillSlots("P_dead_unmaintained", "%.6g" % P_dead_unmaintained)
        ctx.fillSlots("P_dead_maintained", "%.6g" % P_dead_maintained)
        return ctx.tag

    def render_report_span(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        return ctx.tag[yandm(when)]

    def render_P_loss_unmaintained(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        return ctx.tag["%.6g (%1.8f%%)" % (P_dead_unmaintained,
                                           100*P_dead_unmaintained)]

    def render_P_loss_maintained(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        return ctx.tag["%.6g (%1.8f%%)" % (P_dead_maintained,
                                           100*P_dead_maintained)]

    def render_P_repair_rate(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        freq = when / cumulative_number_of_repairs
        return ctx.tag["%.6g" % freq]

    def render_P_repair_shares(self, ctx, row):
        (when, unmaintained_shareprobs, maintained_shareprobs,
         P_repaired_last_check_period,
         cumulative_number_of_repairs,
         cumulative_number_of_new_shares,
         P_dead_unmaintained, P_dead_maintained) = self.results.samples[-1]
        generated_shares = cumulative_number_of_new_shares / cumulative_number_of_repairs
        return ctx.tag["%1.2f" % generated_shares]


