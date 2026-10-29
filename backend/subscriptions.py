"""Account-bound unsubscribe actions from authenticated publisher mail."""
import threading
from datetime import datetime, timezone

from .retrieval import PublicFetcher, SourceError
from .source_lifecycle import category

UNSUBSCRIBE_LOCK = threading.Lock()
STOPPED = ("unsubscribe_pending", "unsubscribing", "unsubscribed")


class Subscriptions:
    def __init__(self, store, fetcher=None):
        self.store, self.fetcher = store, fetcher or PublicFetcher()

    def unsubscribe(self, user_id, source_id):
        with UNSUBSCRIBE_LOCK:
            return self._unsubscribe(user_id, source_id)

    def _unsubscribe(self, user_id, source_id):
        with self.store.connect() as db:
            source = db.execute("SELECT * FROM sources WHERE id=? AND user_id=?", (source_id, user_id)).fetchone()
            if not source:
                raise SourceError("Newsletter not found")
            if source["subscription_status"] == "unsubscribed":
                return {"status": "unsubscribed", "detail": "This newsletter is already unsubscribed"}
            action = db.execute("SELECT * FROM source_mail_actions WHERE source_id=? AND user_id=?", (source_id, user_id)).fetchone()
            db.execute("UPDATE sources SET subscription_status='unsubscribe_pending',subscription_detail='New post processing stopped; publisher unsubscribe is pending' WHERE id=? AND user_id=?", (source_id, user_id))
            db.execute("UPDATE jobs SET status='cancelled',stage='cancelled',error='Cancelled by newsletter unsubscribe',payload=NULL WHERE source_id=? AND user_id=? AND status='queued' AND kind!='visual'", (source_id, user_id))
        if not action:
            if category(dict(source)) == 'custom':
                detail="Email processing stopped. Disable the scheduled task in the sending app if you also want it to stop sending mail. Existing posts are kept."
                with self.store.connect() as db:
                    db.execute("UPDATE sources SET subscription_status='unsubscribed',subscription_detail=?,unsubscribed_at=?,archived_at=NULL WHERE id=? AND user_id=?",(detail,datetime.now(timezone.utc).isoformat(),source_id,user_id))
                return {"status":"unsubscribed","detail":detail}
            return {"status": "unsubscribe_pending", "detail": "New posts are stopped. Publisher unsubscribe needs the unsubscribe instructions from a delivered newsletter email."}
        if not action["one_click"]:
            return {"status": "unsubscribe_pending", "detail": "New posts are stopped. Finish unsubscribing on the publisher page that opens.", "action_url": action["unsubscribe_url"]}
        try:
            response = self.fetcher.submit_form(action["unsubscribe_url"], b"List-Unsubscribe=One-Click")
            if not 200 <= response.status < 300:
                raise SourceError("Publisher unsubscribe requires additional confirmation")
        except SourceError:
            return {"status": "unsubscribe_pending", "detail": "New posts are stopped. Finish unsubscribing on the publisher page.", "action_url": action['unsubscribe_url']}
        detail = "Unsubscribe accepted by the publisher. Existing posts remain in your feed; future editions will not create posts."
        with self.store.connect() as db:
            db.execute("UPDATE sources SET subscription_status='unsubscribed',subscription_detail=?,unsubscribed_at=?,archived_at=NULL WHERE id=? AND user_id=?", (detail, datetime.now(timezone.utc).isoformat(), source_id, user_id))
        return {"status": "unsubscribed", "detail": detail}
