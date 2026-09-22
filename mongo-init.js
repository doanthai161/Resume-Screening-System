const databaseName = process.env.MONGO_APP_DATABASE || "resume_screening_db";
const username = process.env.MONGO_APP_USERNAME;
const password = process.env.MONGO_APP_PASSWORD;

if (!username || !password) {
  throw new Error("MONGO_APP_USERNAME and MONGO_APP_PASSWORD are required");
}

const applicationDatabase = db.getSiblingDB(databaseName);
if (!applicationDatabase.getUser(username)) {
  applicationDatabase.createUser({
    user: username,
    pwd: password,
    roles: [{ role: "readWrite", db: databaseName }],
  });
}
