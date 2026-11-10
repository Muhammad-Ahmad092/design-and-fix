const express = require("express");
const { requireAuth } = require("./auth");
const orders = require("./routes/orders");
const items = require("./routes/items");

const app = express();
app.use(express.json());
app.get("/health", (req, res) => res.send("ok"));
app.use("/api/orders", requireAuth, orders);
app.use("/api/items", items);
app.listen(3000);
