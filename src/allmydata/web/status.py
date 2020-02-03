
import pprint, itertools, hashlib
import json
from twisted.internet import defer
from twisted.web.resource import Resource
from nevow import rend, tags as T
from allmydata.util import base32, idlib
from allmydata.web.common import (
    getxmlfile,
    abbreviate_time,
    abbreviate_rate,
    abbreviate_size,
    plural,
    compute_rate,
    render_time,
    MultiFormatPage,
)
from allmydata.interfaces import IUploadStatus, IDownloadStatus, \
     IPublishStatus, IRetrieveStatus, IServermapUpdaterStatus

class RateAndTimeMixin(object):

    def render_time(self, ctx, data):
        return abbreviate_time(data)

    def render_rate(self, ctx, data):
        return abbreviate_rate(data)

class UploadResultsRendererMixin(RateAndTimeMixin):
    # this requires a method named 'upload_results'

    def render_pushed_shares(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_pushed_shares())
        return d

    def render_preexisting_shares(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_preexisting_shares())
        return d

    def render_sharemap(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_sharemap())
        def _render(sharemap):
            if sharemap is None:
                return "None"
            l = T.ul()
            for shnum, servers in sorted(sharemap.items()):
                server_names = ', '.join([s.get_name() for s in servers])
                l[T.li["%d -> placed on [%s]" % (shnum, server_names)]]
            return l
        d.addCallback(_render)
        return d

    def render_servermap(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_servermap())
        def _render(servermap):
            if servermap is None:
                return "None"
            l = T.ul()
            for server, shnums in sorted(servermap.items()):
                shares_s = ",".join(["#%d" % shnum for shnum in shnums])
                l[T.li["[%s] got share%s: %s" % (server.get_name(),
                                                 plural(shnums), shares_s)]]
            return l
        d.addCallback(_render)
        return d

    def data_file_size(self, ctx, data):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_file_size())
        return d

    def _get_time(self, name):
        d = self.upload_results()
        d.addCallback(lambda res: res.get_timings().get(name))
        return d

    def data_time_total(self, ctx, data):
        return self._get_time("total")

    def data_time_storage_index(self, ctx, data):
        return self._get_time("storage_index")

    def data_time_contacting_helper(self, ctx, data):
        return self._get_time("contacting_helper")

    def data_time_cumulative_fetch(self, ctx, data):
        return self._get_time("cumulative_fetch")

    def data_time_helper_total(self, ctx, data):
        return self._get_time("helper_total")

    def data_time_peer_selection(self, ctx, data):
        return self._get_time("peer_selection")

    def data_time_total_encode_and_push(self, ctx, data):
        return self._get_time("total_encode_and_push")

    def data_time_cumulative_encoding(self, ctx, data):
        return self._get_time("cumulative_encoding")

    def data_time_cumulative_sending(self, ctx, data):
        return self._get_time("cumulative_sending")

    def data_time_hashes_and_close(self, ctx, data):
        return self._get_time("hashes_and_close")

    def _get_rate(self, name):
        d = self.upload_results()
        def _convert(r):
            file_size = r.get_file_size()
            duration = r.get_timings().get(name)
            return compute_rate(file_size, duration)
        d.addCallback(_convert)
        return d

    def data_rate_total(self, ctx, data):
        return self._get_rate("total")

    def data_rate_storage_index(self, ctx, data):
        return self._get_rate("storage_index")

    def data_rate_encode(self, ctx, data):
        return self._get_rate("cumulative_encoding")

    def data_rate_push(self, ctx, data):
        return self._get_rate("cumulative_sending")

    def data_rate_encode_and_push(self, ctx, data):
        d = self.upload_results()
        def _convert(r):
            file_size = r.get_file_size()
            time1 = r.get_timings().get("cumulative_encoding")
            time2 = r.get_timings().get("cumulative_sending")
            if (time1 is None or time2 is None):
                return None
            else:
                return compute_rate(file_size, time1+time2)
        d.addCallback(_convert)
        return d

    def data_rate_ciphertext_fetch(self, ctx, data):
        d = self.upload_results()
        def _convert(r):
            fetch_size = r.get_ciphertext_fetched()
            duration = r.get_timings().get("cumulative_fetch")
            return compute_rate(fetch_size, duration)
        d.addCallback(_convert)
        return d

class UploadStatusPage(UploadResultsRendererMixin, rend.Page):
    docFactory = getxmlfile("upload-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.upload_status = data

    def upload_results(self):
        return defer.maybeDeferred(self.upload_status.get_results)

    def render_results(self, ctx, data):
        d = self.upload_results()
        def _got_results(results):
            if results:
                return ctx.tag
            return ""
        d.addCallback(_got_results)
        return d

    def render_started(self, ctx, data):
        started_s = render_time(data.get_started())
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_total_size(self, ctx, data):
        size = data.get_size()
        if size is None:
            return "(unknown)"
        return size

    def render_progress_hash(self, ctx, data):
        progress = data.get_progress()[0]
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_progress_ciphertext(self, ctx, data):
        progress = data.get_progress()[1]
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_progress_encode_push(self, ctx, data):
        progress = data.get_progress()[2]
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

class DownloadResultsRendererMixin(RateAndTimeMixin):
    # this requires a method named 'download_results'

    def render_servermap(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.servermap)
        def _render(servermap):
            if servermap is None:
                return "None"
            l = T.ul()
            for peerid in sorted(servermap.keys()):
                peerid_s = idlib.shortnodeid_b2a(peerid)
                shares_s = ",".join(["#%d" % shnum
                                     for shnum in servermap[peerid]])
                l[T.li["[%s] has share%s: %s" % (peerid_s,
                                                 plural(servermap[peerid]),
                                                 shares_s)]]
            return l
        d.addCallback(_render)
        return d

    def render_servers_used(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.servers_used)
        def _got(servers_used):
            if not servers_used:
                return ""
            peerids_s = ", ".join(["[%s]" % idlib.shortnodeid_b2a(peerid)
                                   for peerid in servers_used])
            return T.li["Servers Used: ", peerids_s]
        d.addCallback(_got)
        return d

    def render_problems(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.server_problems)
        def _got(server_problems):
            if not server_problems:
                return ""
            l = T.ul()
            for peerid in sorted(server_problems.keys()):
                peerid_s = idlib.shortnodeid_b2a(peerid)
                l[T.li["[%s]: %s" % (peerid_s, server_problems[peerid])]]
            return T.li["Server Problems:", l]
        d.addCallback(_got)
        return d

    def data_file_size(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.file_size)
        return d

    def _get_time(self, name):
        d = self.download_results()
        d.addCallback(lambda res: res.timings.get(name))
        return d

    def data_time_total(self, ctx, data):
        return self._get_time("total")

    def data_time_peer_selection(self, ctx, data):
        return self._get_time("peer_selection")

    def data_time_uri_extension(self, ctx, data):
        return self._get_time("uri_extension")

    def data_time_hashtrees(self, ctx, data):
        return self._get_time("hashtrees")

    def data_time_segments(self, ctx, data):
        return self._get_time("segments")

    def data_time_cumulative_fetch(self, ctx, data):
        return self._get_time("cumulative_fetch")

    def data_time_cumulative_decode(self, ctx, data):
        return self._get_time("cumulative_decode")

    def data_time_cumulative_decrypt(self, ctx, data):
        return self._get_time("cumulative_decrypt")

    def data_time_paused(self, ctx, data):
        return self._get_time("paused")

    def _get_rate(self, name):
        d = self.download_results()
        def _convert(r):
            file_size = r.file_size
            duration = r.timings.get(name)
            return compute_rate(file_size, duration)
        d.addCallback(_convert)
        return d

    def data_rate_total(self, ctx, data):
        return self._get_rate("total")

    def data_rate_segments(self, ctx, data):
        return self._get_rate("segments")

    def data_rate_fetch(self, ctx, data):
        return self._get_rate("cumulative_fetch")

    def data_rate_decode(self, ctx, data):
        return self._get_rate("cumulative_decode")

    def data_rate_decrypt(self, ctx, data):
        return self._get_rate("cumulative_decrypt")

    def render_server_timings(self, ctx, data):
        d = self.download_results()
        d.addCallback(lambda res: res.timings.get("fetch_per_server"))
        def _render(per_server):
            if per_server is None:
                return ""
            l = T.ul()
            for peerid in sorted(per_server.keys()):
                peerid_s = idlib.shortnodeid_b2a(peerid)
                times_s = ", ".join([self.render_time(None, t)
                                     for t in per_server[peerid]])
                l[T.li["[%s]: %s" % (peerid_s, times_s)]]
            return T.li["Per-Server Segment Fetch Response Times: ", l]
        d.addCallback(_render)
        return d

def _find_overlap(events, start_key, end_key):
    """
    given a list of event dicts, return a new list in which each event
    has an extra "row" key (an int, starting at 0), and if appropriate
    a "serverid" key (ascii-encoded server id), replacing the "server"
    key. This is a hint to our JS frontend about how to overlap the
    parts of the graph it is drawing.

    we must always make a copy, since we're going to be adding keys
    and don't want to change the original objects. If we're
    stringifying serverids, we'll also be changing the serverid keys.
    """
    new_events = []
    rows = []
    for ev in events:
        ev = ev.copy()
        if ev.has_key('server'):
            ev["serverid"] = ev["server"].get_longname()
            del ev["server"]
        # find an empty slot in the rows
        free_slot = None
        for row,finished in enumerate(rows):
            if finished is not None:
                if ev[start_key] > finished:
                    free_slot = row
                    break
        if free_slot is None:
            free_slot = len(rows)
            rows.append(ev[end_key])
        else:
            rows[free_slot] = ev[end_key]
        ev["row"] = free_slot
        new_events.append(ev)
    return new_events

def _find_overlap_requests(events):
    """
    We compute a three-element 'row tuple' for each event: (serverid,
    shnum, row). All elements are ints. The first is a mapping from
    serverid to group number, the second is a mapping from shnum to
    subgroup number. The third is a row within the subgroup.

    We also return a list of lists of rowcounts, so renderers can decide
    how much vertical space to give to each row.
    """

    serverid_to_group = {}
    groupnum_to_rows = {} # maps groupnum to a table of rows. Each table
                          # is a list with an element for each row number
                          # (int starting from 0) that contains a
                          # finish_time, indicating that the row is empty
                          # beyond that time. If finish_time is None, it
                          # indicate a response that has not yet
                          # completed, so the row cannot be reused.
    new_events = []
    for ev in events:
        # DownloadStatus promises to give us events in temporal order
        ev = ev.copy()
        ev["serverid"] = ev["server"].get_longname()
        del ev["server"]
        if ev["serverid"] not in serverid_to_group:
            groupnum = len(serverid_to_group)
            serverid_to_group[ev["serverid"]] = groupnum
        groupnum = serverid_to_group[ev["serverid"]]
        if groupnum not in groupnum_to_rows:
            groupnum_to_rows[groupnum] = []
        rows = groupnum_to_rows[groupnum]
        # find an empty slot in the rows
        free_slot = None
        for row,finished in enumerate(rows):
            if finished is not None:
                if ev["start_time"] > finished:
                    free_slot = row
                    break
        if free_slot is None:
            free_slot = len(rows)
            rows.append(ev["finish_time"])
        else:
            rows[free_slot] = ev["finish_time"]
        ev["row"] = (groupnum, free_slot)
        new_events.append(ev)
    del groupnum
    # maybe also return serverid_to_group, groupnum_to_rows, and some
    # indication of the highest finish_time
    #
    # actually, return the highest rownum for each groupnum
    highest_rownums = [len(groupnum_to_rows[groupnum])
                       for groupnum in range(len(serverid_to_group))]
    return new_events, highest_rownums


def _color(server):
    h = hashlib.sha256(server.get_serverid()).digest()
    def m(c):
        return min(ord(c) / 2 + 0x80, 0xff)
    return "#%02x%02x%02x" % (m(h[0]), m(h[1]), m(h[2]))

class _EventJson(Resource, object):

    def __init__(self, download_status):
        self._download_status = download_status

    def render(self, request):
        request.setHeader("content-type", "text/plain")
        data = { } # this will be returned to the GET
        ds = self._download_status

        data["misc"] = _find_overlap(
            ds.misc_events,
            "start_time", "finish_time",
        )
        data["read"] = _find_overlap(
            ds.read_events,
            "start_time", "finish_time",
        )
        data["segment"] = _find_overlap(
            ds.segment_events,
            "start_time", "finish_time",
        )
        # TODO: overlap on DYHB isn't very useful, and usually gets in the
        # way. So don't do it.
        data["dyhb"] = _find_overlap(
            ds.dyhb_requests,
            "start_time", "finish_time",
        )
        data["block"],data["block_rownums"] =_find_overlap_requests(ds.block_requests)

        server_info = {} # maps longname to {num,color,short}
        server_shortnames = {} # maps servernum to shortname
        for d_ev in ds.dyhb_requests:
            s = d_ev["server"]
            longname = s.get_longname()
            if longname not in server_info:
                num = len(server_info)
                server_info[longname] = {"num": num,
                                         "color": _color(s),
                                         "short": s.get_name() }
                server_shortnames[str(num)] = s.get_name()

        data["server_info"] = server_info
        data["num_serverids"] = len(server_info)
        # we'd prefer the keys of serverids[] to be ints, but this is JSON,
        # so they get converted to strings. Stupid javascript.
        data["serverids"] = server_shortnames
        data["bounds"] = {"min": ds.first_timestamp, "max": ds.last_timestamp}
        return json.dumps(data, indent=1) + "\n"


class DownloadStatusPage(DownloadResultsRendererMixin, rend.Page):
    docFactory = getxmlfile("download-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.download_status = data
        self.putChild("event_json", _EventJson(self.download_status))

    def download_results(self):
        return defer.maybeDeferred(self.download_status.get_results)

    def relative_time(self, t):
        if t is None:
            return t
        if self.download_status.first_timestamp is not None:
            return t - self.download_status.first_timestamp
        return t
    def short_relative_time(self, t):
        t = self.relative_time(t)
        if t is None:
            return ""
        return "+%.6fs" % t

    def render_timeline_link(self, ctx, data):
        from nevow import url
        return T.a(href=url.URL.fromContext(ctx).child("timeline"))["timeline"]

    def _rate_and_time(self, bytes, seconds):
        time_s = self.render_time(None, seconds)
        if seconds != 0:
            rate = self.render_rate(None, 1.0 * bytes / seconds)
            return T.span(title=rate)[time_s]
        return T.span[time_s]

    def render_events(self, ctx, data):
        if not self.download_status.storage_index:
            return
        srt = self.short_relative_time
        l = T.div()

        t = T.table(align="left", class_="status-download-events")
        t[T.tr[T.th["serverid"], T.th["sent"], T.th["received"],
               T.th["shnums"], T.th["RTT"]]]
        for d_ev in self.download_status.dyhb_requests:
            server = d_ev["server"]
            sent = d_ev["start_time"]
            shnums = d_ev["response_shnums"]
            received = d_ev["finish_time"]
            rtt = None
            if received is not None:
                rtt = received - sent
            if not shnums:
                shnums = ["-"]
            t[T.tr(style="background: %s" % _color(server))[
                [T.td[server.get_name()], T.td[srt(sent)], T.td[srt(received)],
                 T.td[",".join([str(shnum) for shnum in shnums])],
                 T.td[self.render_time(None, rtt)],
                 ]]]

        l[T.h2["DYHB Requests:"], t]
        l[T.br(clear="all")]

        t = T.table(align="left",class_="status-download-events")
        t[T.tr[T.th["range"], T.th["start"], T.th["finish"], T.th["got"],
               T.th["time"], T.th["decrypttime"], T.th["pausedtime"],
               T.th["speed"]]]
        for r_ev in self.download_status.read_events:
            start = r_ev["start"]
            length = r_ev["length"]
            bytes = r_ev["bytes_returned"]
            decrypt_time = ""
            if bytes:
                decrypt_time = self._rate_and_time(bytes, r_ev["decrypt_time"])
            speed, rtt = "",""
            if r_ev["finish_time"] is not None:
                rtt = r_ev["finish_time"] - r_ev["start_time"] - r_ev["paused_time"]
                speed = self.render_rate(None, compute_rate(bytes, rtt))
                rtt = self.render_time(None, rtt)
            paused = self.render_time(None, r_ev["paused_time"])

            t[T.tr[T.td["[%d:+%d]" % (start, length)],
                   T.td[srt(r_ev["start_time"])], T.td[srt(r_ev["finish_time"])],
                   T.td[bytes], T.td[rtt],
                   T.td[decrypt_time], T.td[paused],
                   T.td[speed],
                   ]]

        l[T.h2["Read Events:"], t]
        l[T.br(clear="all")]

        t = T.table(align="left",class_="status-download-events")
        t[T.tr[T.th["segnum"], T.th["start"], T.th["active"], T.th["finish"],
               T.th["range"],
               T.th["decodetime"], T.th["segtime"], T.th["speed"]]]
        for s_ev in self.download_status.segment_events:
            range_s = "-"
            segtime_s = "-"
            speed = "-"
            decode_time = "-"
            if s_ev["finish_time"] is not None:
                if s_ev["success"]:
                    segtime = s_ev["finish_time"] - s_ev["active_time"]
                    segtime_s = self.render_time(None, segtime)
                    seglen = s_ev["segment_length"]
                    range_s = "[%d:+%d]" % (s_ev["segment_start"], seglen)
                    speed = self.render_rate(None, compute_rate(seglen, segtime))
                    decode_time = self._rate_and_time(seglen, s_ev["decode_time"])
                else:
                    # error
                    range_s = "error"
            else:
                # not finished yet
                pass

            t[T.tr[T.td["seg%d" % s_ev["segment_number"]],
                   T.td[srt(s_ev["start_time"])],
                   T.td[srt(s_ev["active_time"])],
                   T.td[srt(s_ev["finish_time"])],
                   T.td[range_s],
                   T.td[decode_time],
                   T.td[segtime_s], T.td[speed]]]

        l[T.h2["Segment Events:"], t]
        l[T.br(clear="all")]
        t = T.table(align="left",class_="status-download-events")
        t[T.tr[T.th["serverid"], T.th["shnum"], T.th["range"],
               T.th["txtime"], T.th["rxtime"],
               T.th["received"], T.th["RTT"]]]
        for r_ev in self.download_status.block_requests:
            server = r_ev["server"]
            rtt = None
            if r_ev["finish_time"] is not None:
                rtt = r_ev["finish_time"] - r_ev["start_time"]
            color = _color(server)
            t[T.tr(style="background: %s" % color)[
                T.td[server.get_name()], T.td[r_ev["shnum"]],
                T.td["[%d:+%d]" % (r_ev["start"], r_ev["length"])],
                T.td[srt(r_ev["start_time"])], T.td[srt(r_ev["finish_time"])],
                T.td[r_ev["response_length"] or ""],
                T.td[self.render_time(None, rtt)],
                ]]

        l[T.h2["Requests:"], t]
        l[T.br(clear="all")]

        return l

    def render_results(self, ctx, data):
        d = self.download_results()
        def _got_results(results):
            if results:
                return ctx.tag
            return ""
        d.addCallback(_got_results)
        return d

    def render_started(self, ctx, data):
        started_s = render_time(data.get_started())
        return started_s + " (%s)" % data.get_started()

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_total_size(self, ctx, data):
        size = data.get_size()
        if size is None:
            return "(unknown)"
        return size

    def render_progress(self, ctx, data):
        progress = data.get_progress()
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

class RetrieveStatusPage(rend.Page, RateAndTimeMixin):
    docFactory = getxmlfile("retrieve-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.retrieve_status = data

    def render_started(self, ctx, data):
        started_s = render_time(data.get_started())
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_current_size(self, ctx, data):
        size = data.get_size()
        if size is None:
            size = "(unknown)"
        return size

    def render_progress(self, ctx, data):
        progress = data.get_progress()
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

    def render_encoding(self, ctx, data):
        k, n = data.get_encoding()
        return ctx.tag["Encoding: %s of %s" % (k, n)]

    def render_problems(self, ctx, data):
        problems = data.get_problems()
        if not problems:
            return ""
        l = T.ul()
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            l[T.li["[%s]: %s" % (peerid_s, problems[peerid])]]
        return ctx.tag["Server Problems:", l]

    def _get_rate(self, data, name):
        file_size = self.retrieve_status.get_size()
        duration = self.retrieve_status.timings.get(name)
        return compute_rate(file_size, duration)

    def data_time_total(self, ctx, data):
        return self.retrieve_status.timings.get("total")
    def data_rate_total(self, ctx, data):
        return self._get_rate(data, "total")

    def data_time_fetch(self, ctx, data):
        return self.retrieve_status.timings.get("fetch")
    def data_rate_fetch(self, ctx, data):
        return self._get_rate(data, "fetch")

    def data_time_decode(self, ctx, data):
        return self.retrieve_status.timings.get("decode")
    def data_rate_decode(self, ctx, data):
        return self._get_rate(data, "decode")

    def data_time_decrypt(self, ctx, data):
        return self.retrieve_status.timings.get("decrypt")
    def data_rate_decrypt(self, ctx, data):
        return self._get_rate(data, "decrypt")

    def render_server_timings(self, ctx, data):
        per_server = self.retrieve_status.timings.get("fetch_per_server")
        if not per_server:
            return ""
        l = T.ul()
        for server in sorted(per_server.keys(), key=lambda s: s.get_name()):
            times_s = ", ".join([self.render_time(None, t)
                                 for t in per_server[server]])
            l[T.li["[%s]: %s" % (server.get_name(), times_s)]]
        return T.li["Per-Server Fetch Response Times: ", l]


class PublishStatusPage(rend.Page, RateAndTimeMixin):
    docFactory = getxmlfile("publish-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.publish_status = data

    def render_started(self, ctx, data):
        started_s = render_time(data.get_started())
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_current_size(self, ctx, data):
        size = data.get_size()
        if size is None:
            size = "(unknown)"
        return size

    def render_progress(self, ctx, data):
        progress = data.get_progress()
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

    def render_encoding(self, ctx, data):
        k, n = data.get_encoding()
        return ctx.tag["Encoding: %s of %s" % (k, n)]

    def render_sharemap(self, ctx, data):
        servermap = data.get_servermap()
        if servermap is None:
            return ctx.tag["None"]
        l = T.ul()
        sharemap = servermap.make_sharemap()
        for shnum in sorted(sharemap.keys()):
            l[T.li["%d -> Placed on " % shnum,
                   ", ".join(["[%s]" % server.get_name()
                              for server in sharemap[shnum]])]]
        return ctx.tag["Sharemap:", l]

    def render_problems(self, ctx, data):
        problems = data.get_problems()
        if not problems:
            return ""
        l = T.ul()
        # XXX: is this exercised? I don't think PublishStatus.problems is
        # ever populated
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            l[T.li["[%s]: %s" % (peerid_s, problems[peerid])]]
        return ctx.tag["Server Problems:", l]

    def _get_rate(self, data, name):
        file_size = self.publish_status.get_size()
        duration = self.publish_status.timings.get(name)
        return compute_rate(file_size, duration)

    def data_time_total(self, ctx, data):
        return self.publish_status.timings.get("total")
    def data_rate_total(self, ctx, data):
        return self._get_rate(data, "total")

    def data_time_setup(self, ctx, data):
        return self.publish_status.timings.get("setup")

    def data_time_encrypt(self, ctx, data):
        return self.publish_status.timings.get("encrypt")
    def data_rate_encrypt(self, ctx, data):
        return self._get_rate(data, "encrypt")

    def data_time_encode(self, ctx, data):
        return self.publish_status.timings.get("encode")
    def data_rate_encode(self, ctx, data):
        return self._get_rate(data, "encode")

    def data_time_pack(self, ctx, data):
        return self.publish_status.timings.get("pack")
    def data_rate_pack(self, ctx, data):
        return self._get_rate(data, "pack")
    def data_time_sign(self, ctx, data):
        return self.publish_status.timings.get("sign")

    def data_time_push(self, ctx, data):
        return self.publish_status.timings.get("push")
    def data_rate_push(self, ctx, data):
        return self._get_rate(data, "push")

    def render_server_timings(self, ctx, data):
        per_server = self.publish_status.timings.get("send_per_server")
        if not per_server:
            return ""
        l = T.ul()
        for server in sorted(per_server.keys(), key=lambda s: s.get_name()):
            times_s = ", ".join([self.render_time(None, t)
                                 for t in per_server[server]])
            l[T.li["[%s]: %s" % (server.get_name(), times_s)]]
        return T.li["Per-Server Response Times: ", l]

class MapupdateStatusPage(rend.Page, RateAndTimeMixin):
    docFactory = getxmlfile("map-update-status.xhtml")

    def __init__(self, data):
        rend.Page.__init__(self, data)
        self.update_status = data

    def render_started(self, ctx, data):
        started_s = render_time(data.get_started())
        return started_s

    def render_finished(self, ctx, data):
        when = data.get_finished()
        if not when:
            return "not yet"
        started_s = render_time(data.get_finished())
        return started_s

    def render_si(self, ctx, data):
        si_s = base32.b2a_or_none(data.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        return si_s

    def render_helper(self, ctx, data):
        return {True: "Yes",
                False: "No"}[data.using_helper()]

    def render_progress(self, ctx, data):
        progress = data.get_progress()
        # TODO: make an ascii-art bar
        return "%.1f%%" % (100.0 * progress)

    def render_status(self, ctx, data):
        return data.get_status()

    def render_problems(self, ctx, data):
        problems = data.problems
        if not problems:
            return ""
        l = T.ul()
        for peerid in sorted(problems.keys()):
            peerid_s = idlib.shortnodeid_b2a(peerid)
            l[T.li["[%s]: %s" % (peerid_s, problems[peerid])]]
        return ctx.tag["Server Problems:", l]

    def render_privkey_from(self, ctx, data):
        server = data.get_privkey_from()
        if server:
            return ctx.tag["Got privkey from: [%s]" % server.get_name()]
        else:
            return ""

    def data_time_total(self, ctx, data):
        return self.update_status.timings.get("total")

    def data_time_initial_queries(self, ctx, data):
        return self.update_status.timings.get("initial_queries")

    def data_time_cumulative_verify(self, ctx, data):
        return self.update_status.timings.get("cumulative_verify")

    def render_server_timings(self, ctx, data):
        per_server = self.update_status.timings.get("per_server")
        if not per_server:
            return ""
        l = T.ul()
        for server in sorted(per_server.keys(), key=lambda s: s.get_name()):
            times = []
            for op,started,t in per_server[server]:
                #times.append("%s/%.4fs/%s/%s" % (op,
                #                              started,
                #                              self.render_time(None, started - self.update_status.get_started()),
                #                              self.render_time(None,t)))
                if op == "query":
                    times.append( self.render_time(None, t) )
                elif op == "late":
                    times.append( "late(" + self.render_time(None, t) + ")" )
                else:
                    times.append( "privkey(" + self.render_time(None, t) + ")" )
            times_s = ", ".join(times)
            l[T.li["[%s]: %s" % (server.get_name(), times_s)]]
        return T.li["Per-Server Response Times: ", l]


def marshal_json(s):
    # common item data
    item = {
        "storage-index-string": base32.b2a_or_none(s.get_storage_index()),
        "total-size": s.get_size(),
        "status": s.get_status(),
    }

    # type-specific item date
    if IUploadStatus.providedBy(s):
        h, c, e = s.get_progress()
        item["type"] = "upload"
        item["progress-hash"] = h
        item["progress-ciphertext"] = c
        item["progress-encode-push"] = e

    elif IDownloadStatus.providedBy(s):
        item["type"] = "download"
        item["progress"] = s.get_progress()

    elif IPublishStatus.providedBy(s):
        item["type"] = "publish"

    elif IRetrieveStatus.providedBy(s):
        item["type"] = "retrieve"

    elif IServermapUpdaterStatus.providedBy(s):
        item["type"] = "mapupdate"
        item["mode"] = s.get_mode()

    else:
        item["type"] = "unknown"
        item["class"] = s.__class__.__name__

    return item


class Status(MultiFormatPage):
    docFactory = getxmlfile("status.xhtml")
    addSlash = True

    def __init__(self, history):
        rend.Page.__init__(self, history)
        self.history = history

    def render_JSON(self, req):
        # modern browsers now render this instead of forcing downloads
        req.setHeader("content-type", "application/json")
        data = {}
        data["active"] = active = []
        data["recent"] = recent = []

        for s in self._get_active_operations():
            active.append(marshal_json(s))

        for s in self._get_recent_operations():
            recent.append(marshal_json(s))

        return json.dumps(data, indent=1) + "\n"

    def _get_all_statuses(self):
        h = self.history
        return itertools.chain(h.list_all_upload_statuses(),
                               h.list_all_download_statuses(),
                               h.list_all_mapupdate_statuses(),
                               h.list_all_publish_statuses(),
                               h.list_all_retrieve_statuses(),
                               h.list_all_helper_statuses(),
                               )

    def data_active_operations(self, ctx, data):
        return self._get_active_operations()

    def _get_active_operations(self):
        active = [s
                  for s in self._get_all_statuses()
                  if s.get_active()]
        active.sort(lambda a, b: cmp(a.get_started(), b.get_started()))
        active.reverse()
        return active

    def data_recent_operations(self, ctx, data):
        return self._get_recent_operations()

    def _get_recent_operations(self):
        recent = [s
                  for s in self._get_all_statuses()
                  if not s.get_active()]
        recent.sort(lambda a, b: cmp(a.get_started(), b.get_started()))
        recent.reverse()
        return recent

    def render_row(self, ctx, data):
        s = data

        started_s = render_time(s.get_started())
        ctx.fillSlots("started", started_s)

        si_s = base32.b2a_or_none(s.get_storage_index())
        if si_s is None:
            si_s = "(None)"
        ctx.fillSlots("si", si_s)
        ctx.fillSlots("helper", {True: "Yes",
                                 False: "No"}[s.using_helper()])

        size = s.get_size()
        if size is None:
            size = "(unknown)"
        elif isinstance(size, (int, long, float)):
            size = abbreviate_size(size)
        ctx.fillSlots("total_size", size)

        progress = data.get_progress()
        if IUploadStatus.providedBy(data):
            link = "up-%d" % data.get_counter()
            ctx.fillSlots("type", "upload")
            # TODO: make an ascii-art bar
            (chk, ciphertext, encandpush) = progress
            progress_s = ("hash: %.1f%%, ciphertext: %.1f%%, encode: %.1f%%" %
                          ( (100.0 * chk),
                            (100.0 * ciphertext),
                            (100.0 * encandpush) ))
            ctx.fillSlots("progress", progress_s)
        elif IDownloadStatus.providedBy(data):
            link = "down-%d" % data.get_counter()
            ctx.fillSlots("type", "download")
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        elif IPublishStatus.providedBy(data):
            link = "publish-%d" % data.get_counter()
            ctx.fillSlots("type", "publish")
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        elif IRetrieveStatus.providedBy(data):
            ctx.fillSlots("type", "retrieve")
            link = "retrieve-%d" % data.get_counter()
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        else:
            assert IServermapUpdaterStatus.providedBy(data)
            ctx.fillSlots("type", "mapupdate %s" % data.get_mode())
            link = "mapupdate-%d" % data.get_counter()
            ctx.fillSlots("progress", "%.1f%%" % (100.0 * progress))
        ctx.fillSlots("status", T.a(href=link)[s.get_status()])
        return ctx.tag

    def childFactory(self, ctx, name):
        h = self.history
        try:
            stype, count_s = name.split("-")
        except ValueError:
            raise RuntimeError(
                "no - in '{}'".format(name)
            )
        count = int(count_s)
        if stype == "up":
            for s in itertools.chain(h.list_all_upload_statuses(),
                                     h.list_all_helper_statuses()):
                # immutable-upload helpers use the same status object as a
                # regular immutable-upload
                if s.get_counter() == count:
                    return UploadStatusPage(s)
        if stype == "down":
            for s in h.list_all_download_statuses():
                if s.get_counter() == count:
                    return DownloadStatusPage(s)
        if stype == "mapupdate":
            for s in h.list_all_mapupdate_statuses():
                if s.get_counter() == count:
                    return MapupdateStatusPage(s)
        if stype == "publish":
            for s in h.list_all_publish_statuses():
                if s.get_counter() == count:
                    return PublishStatusPage(s)
        if stype == "retrieve":
            for s in h.list_all_retrieve_statuses():
                if s.get_counter() == count:
                    return RetrieveStatusPage(s)


class HelperStatus(MultiFormatPage):
    docFactory = getxmlfile("helper.xhtml")

    def __init__(self, helper):
        rend.Page.__init__(self, helper)
        self.helper = helper

    def data_helper_stats(self, ctx, data):
        return self.helper.get_stats()

    def render_JSON(self, req):
        req.setHeader("content-type", "text/plain")
        if self.helper:
            stats = self.helper.get_stats()
            return json.dumps(stats, indent=1) + "\n"
        return json.dumps({}) + "\n"

    def render_active_uploads(self, ctx, data):
        return data["chk_upload_helper.active_uploads"]

    def render_incoming(self, ctx, data):
        return "%d bytes in %d files" % (data["chk_upload_helper.incoming_size"],
                                         data["chk_upload_helper.incoming_count"])

    def render_encoding(self, ctx, data):
        return "%d bytes in %d files" % (data["chk_upload_helper.encoding_size"],
                                         data["chk_upload_helper.encoding_count"])

    def render_upload_requests(self, ctx, data):
        return str(data["chk_upload_helper.upload_requests"])

    def render_upload_already_present(self, ctx, data):
        return str(data["chk_upload_helper.upload_already_present"])

    def render_upload_need_upload(self, ctx, data):
        return str(data["chk_upload_helper.upload_need_upload"])

    def render_upload_bytes_fetched(self, ctx, data):
        return str(data["chk_upload_helper.fetched_bytes"])

    def render_upload_bytes_encoded(self, ctx, data):
        return str(data["chk_upload_helper.encoded_bytes"])


class Statistics(MultiFormatPage):
    docFactory = getxmlfile("statistics.xhtml")

    def __init__(self, provider):
        rend.Page.__init__(self, provider)
        self.provider = provider

    def render_JSON(self, req):
        stats = self.provider.get_stats()
        req.setHeader("content-type", "text/plain")
        return json.dumps(stats, indent=1) + "\n"

    def data_get_stats(self, ctx, data):
        return self.provider.get_stats()

    def render_load_average(self, ctx, data):
        return str(data["stats"].get("load_monitor.avg_load"))

    def render_peak_load(self, ctx, data):
        return str(data["stats"].get("load_monitor.max_load"))

    def render_uploads(self, ctx, data):
        files = data["counters"].get("uploader.files_uploaded", 0)
        bytes = data["counters"].get("uploader.bytes_uploaded", 0)
        return ("%s files / %s bytes (%s)" %
                (files, bytes, abbreviate_size(bytes)))

    def render_downloads(self, ctx, data):
        files = data["counters"].get("downloader.files_downloaded", 0)
        bytes = data["counters"].get("downloader.bytes_downloaded", 0)
        return ("%s files / %s bytes (%s)" %
                (files, bytes, abbreviate_size(bytes)))

    def render_publishes(self, ctx, data):
        files = data["counters"].get("mutable.files_published", 0)
        bytes = data["counters"].get("mutable.bytes_published", 0)
        return "%s files / %s bytes (%s)" % (files, bytes,
                                             abbreviate_size(bytes))

    def render_retrieves(self, ctx, data):
        files = data["counters"].get("mutable.files_retrieved", 0)
        bytes = data["counters"].get("mutable.bytes_retrieved", 0)
        return "%s files / %s bytes (%s)" % (files, bytes,
                                             abbreviate_size(bytes))

    def render_magic_uploader_monitored(self, ctx, data):
        dirs = data["counters"].get("magic_folder.uploader.dirs_monitored", 0)
        return "%s directories" % (dirs,)

    def render_magic_uploader_succeeded(self, ctx, data):
        # TODO: bytes uploaded
        files = data["counters"].get("magic_folder.uploader.objects_succeeded", 0)
        return "%s files" % (files,)

    def render_magic_uploader_queued(self, ctx, data):
        files = data["counters"].get("magic_folder.uploader.objects_queued", 0)
        return "%s files" % (files,)

    def render_magic_uploader_failed(self, ctx, data):
        files = data["counters"].get("magic_folder.uploader.objects_failed", 0)
        return "%s files" % (files,)

    def render_magic_downloader_succeeded(self, ctx, data):
        # TODO: bytes uploaded
        files = data["counters"].get("magic_folder.downloader.objects_succeeded", 0)
        return "%s files" % (files,)

    def render_magic_downloader_queued(self, ctx, data):
        files = data["counters"].get("magic_folder.downloader.objects_queued", 0)
        return "%s files" % (files,)

    def render_magic_downloader_failed(self, ctx, data):
        files = data["counters"].get("magic_folder.downloader.objects_failed", 0)
        return "%s files" % (files,)

    def render_raw(self, ctx, data):
        raw = pprint.pformat(data)
        return ctx.tag[raw]
