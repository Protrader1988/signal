# BOUNTY inbox

Two files that Muse (or anyone) adds rows to, one row per line, by editing on github.com
(open the file, click the pencil icon, add rows at the bottom, "Commit changes").
Every commit here triggers the memo run within minutes.

- `listings.csv`: new listings found on LoopNet, Crexi, Zillow, StreetEasy, Realtor.com, OneKey and saved-search alert emails.
- `zoning.csv`: independent zoning reviews of the sites in `bounty/data/zoning_queue.csv` (use the `id` from that file).

Rules: one row per property, values separated by commas, wrap any value that contains a comma in "double quotes",
leave a cell empty if the official source does not state it.
