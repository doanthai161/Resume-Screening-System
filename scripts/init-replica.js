// Idempotent initialization; existing data and users are retained.
const admin = db.getSiblingDB("admin");
if (!admin.auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD)) {
  throw new Error("MongoDB authentication failed");
}
try {
  rs.status();
} catch (error) {
  if (error.code !== 94) throw error;
  const result = rs.initiate({_id: "rs0", members: [{_id: 0, host: "mongo:27017"}]});
  if (!result.ok) throw new Error("Replica set initialization failed");
}
let ready = false;
for (let attempt = 0; attempt < 60; attempt++) {
  if (admin.runCommand({hello: 1}).isWritablePrimary) { ready = true; break; }
  sleep(1000);
}
if (!ready) throw new Error("Replica set primary election timed out");
