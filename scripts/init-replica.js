// Idempotent initialization; existing data and users are retained.
const admin = db.getSiblingDB("admin");
if (!admin.auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD)) {
  throw new Error("MongoDB authentication failed");
}
try {
  const status = rs.status();
  if (status.set !== "rs0") throw new Error("Unexpected replica set name; expected rs0");
} catch (error) {
  if (error.code !== 94) throw error;
  const result = rs.initiate({_id: "rs0", members: [{_id: 0, host: "mongo:27017"}]});
  if (!result.ok) throw new Error("Replica set initialization failed");
}
let ready = false;
for (let attempt = 0; attempt < 60; attempt++) {
  const hello = admin.runCommand({hello: 1});
  if (hello.setName === "rs0" && hello.isWritablePrimary) { ready = true; break; }
  sleep(1000);
}
if (!ready) throw new Error("Replica set primary election timed out");
