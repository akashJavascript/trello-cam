"""The Onshape panel ("Send to AutoCAM", Phase 1, 2026-11-01; docs/panel.md).

A page in a Part Studio's right panel in Onshape. A student picks a part (in the CAD or from the panel's list),
sets the quantity and options, and presses Send: a part card in AutoCAM's own format lands in Ready for CAM (or
Drafts), exactly as if it had been typed. The panel also shows where each part already is (queued, on a sheet,
cut, sent back and why).

    onshape_reads.py   the panel's Onshape calls: parts lists, assembly counts, setting a material (own limits)
    board.py           part cards on the Trello board: status, making and changing them (no Onshape calls)
    api.py             the requests the page makes (pure: tested without sockets)
    server.py          the HTTP server on 127.0.0.1 and the Cloudflare tunnel that makes it public
    static/            the page itself (plain HTML, CSS and JavaScript; no build step)

Who sent a card: each student types their name once (kept per Onshape user id), no Onshape sign-in (the user's
choice, 2026-11-01). The panel's link carries a secret key (AUTOCAM_PANEL_KEY in .env), and every request
without it is refused.
"""
