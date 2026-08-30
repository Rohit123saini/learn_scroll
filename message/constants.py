# message/constants.py
#
# 🔥 NAYI FILE — `MAX_PINNED_PER_CONVERSATION = 3` pehle do jagah alag-alag
# define tha: `views.py` (`MessageViewSet.pin`) aur `consumers.py`
# (`ChatConsumer.pin_or_unpin_message`). Dono jagah manually sync rakhna
# padta — agar kal ye limit 3 se 5 badhani ho to dono files edit karni
# padtin, aur ek jagah bhool jaane se REST aur WS ka behavior mismatch ho
# jaata (e.g. REST 5 tak allow kare par WS abhi bhi 3 pe rok de).
#
# Ab dono ise yahan se import karte hain — single source of truth.

MAX_PINNED_PER_CONVERSATION = 3