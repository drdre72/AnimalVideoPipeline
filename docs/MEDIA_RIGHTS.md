# Item-specific media rights

Import operator-owned or explicitly licensed local clips only. Record
creator, source URL/reference, license evidence/reference, commercial and
modification permissions, attribution, restrictions, expiry and file hash.
Unknown rights, test patterns and synthetic stand-ins are never publishable.
Commons and government sites need item-specific checking. Do not import
competitor or social-platform videos. Rights records survive failed renders
and revisions. Changing an asset invalidates prior approval.

`commons-search "Octopus cyanea" --allow-network` queries the official
Wikimedia Commons search API for video titles (`filetype:video`) without
downloading media.
`commons-import "File:Octopus.ogv" --species "Day octopus" --allow-network`
fetches item-specific rights metadata, accepts only exact CC BY or CC0
licenses without restrictions, validates pinned HTTPS hosts and redirect
targets, bounds response bytes and stores a hash-checked local video.
Imports remain **unapproved** until an operator checks the original item,
species and attribution and runs `verify-rights <asset_id>`.
The species tag is operator-supplied, not machine-confirmed. Commons metadata
can change, so a new license or creator must be reviewed independently.
