/**
 * BOUNTY listing inbox: Gmail alerts -> Google Sheet (free, runs in the owner's Google account).
 *
 * Setup (one time):
 *  1. Create a Google Sheet named "BOUNTY Listings" with two tabs: "muse" and "alerts".
 *     Row 1 of "muse":   date | address | town | state | price | lot_sf | zoning | approved_units | url | source | notes
 *     Row 1 of "alerts": received | from | subject | url | text
 *  2. Extensions > Apps Script, paste this file, set SHEET_ID below, run setup() once and approve.
 *  3. File > Share > Publish to web: publish each tab as CSV; paste both CSV links into
 *     bounty/data/hunter_config.json in the signal repo (or send them to Claude).
 *
 * Every hour it reads unread Gmail threads labeled "BOUNTY Listings" (the saved-search alerts from
 * LoopNet, Crexi, Zillow, StreetEasy, Realtor, OneKey...), writes one row per listing link, and marks
 * the thread read. BOUNTY reads the published CSV each morning and builds a feasibility memo per listing.
 */
var SHEET_ID = 'PASTE_SHEET_ID_HERE';
var LABEL = 'BOUNTY Listings';
var DOMAINS = /(loopnet\.com|crexi\.com|zillow\.com|streeteasy\.com|realtor\.com|redfin\.com|onekeymls\.com|showcase\.com|commercialcafe\.com|cityfeet\.com|propertyshark\.com|trulia\.com|homes\.com|smartmls\.com)/i;

function setup() {
  GmailApp.getUserLabelByName(LABEL) || GmailApp.createLabel(LABEL);
  ScriptApp.getProjectTriggers().forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('harvest').timeBased().everyHours(1).create();
  harvest();
}

function harvest() {
  var sh = SpreadsheetApp.openById(SHEET_ID).getSheetByName('alerts');
  var seen = {};
  var last = sh.getLastRow();
  if (last > 1) sh.getRange(2, 4, last - 1, 1).getValues().forEach(function (r) { seen[r[0]] = 1; });
  var threads = GmailApp.search('label:"' + LABEL + '" is:unread newer_than:14d', 0, 50);
  var rows = [];
  threads.forEach(function (th) {
    th.getMessages().forEach(function (m) {
      if (!m.isUnread()) return;
      var html = m.getBody();
      var re = /<a[^>]+href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi, a;
      while ((a = re.exec(html)) !== null) {
        var url = a[1].replace(/&amp;/g, '&');
        if (!DOMAINS.test(url)) continue;
        if (/unsubscribe|preferences|privacy|terms|app\.|apps\.apple|play\.google|mailto:/i.test(url)) continue;
        var key = url.split('?')[0];
        if (seen[key]) continue;
        seen[key] = 1;
        var start = Math.max(0, a.index - 600);
        var text = html.slice(start, a.index + 600).replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();
        rows.push([m.getDate(), m.getFrom(), m.getSubject(), key, text.slice(0, 900)]);
      }
      m.markRead();
    });
  });
  if (rows.length) sh.getRange(sh.getLastRow() + 1, 1, rows.length, 5).setValues(rows);
}
