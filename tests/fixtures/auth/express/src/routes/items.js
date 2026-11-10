const express = require("express");
const { requireAuth } = require("../auth");

const router = express.Router();
router.use(requireAuth);

router.post("/", (req, res) => res.status(201).json(req.body));

module.exports = router;
