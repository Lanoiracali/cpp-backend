---
trigger: always_on
---

remember on backend rules:
if making a new endpoint, add only on the v1 (do NOT make a v2 of that endpoint immediately). if the endpoint is to be updated (example, an endpoint from v1 is to be updated) then make a v2 on that endpoint, but only on that function, do not copy the rest of the code into the new version, only the one to be updated.