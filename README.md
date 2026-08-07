# The Indicator — Full History RSS Feed

This repository maintains an RSS feed for NPR's **The Indicator from Planet Money** that preserves episodes after they fall off NPR's official podcast feed.

NPR's official feed only exposes a limited number of recent episodes:

https://feeds.npr.org/510325/podcast.xml

This project keeps a permanent copy and adds new episodes automatically using GitHub Actions.

## Feed

The full-history feed is:

https://drfujisawa.github.io/theindicator-rss/theindicator_feed.xml

## How it works

`theindicator_rss.py` downloads NPR's current RSS feed and merges any new episodes into `theindicator_feed.xml`.

Episodes already stored in the archive are never removed simply because they disappear from NPR's official feed.

A GitHub Actions workflow runs automatically every six hours and updates the archive.

## Historical episodes

The current archive begins with the episodes available in NPR's feed when this project was converted to The Indicator.

Older episodes dating back to the show's launch in 2017 will be added separately.

## Credits

This repository was originally forked from:

https://github.com/xjcl/planetmoney-rss

That project preserves the full history of NPR's Planet Money podcast.
