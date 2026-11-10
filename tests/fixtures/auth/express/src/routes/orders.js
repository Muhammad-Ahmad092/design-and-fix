const express = require("express");

const router = express.Router();

router.post("/", (req, res) => res.status(201).json(req.body));
router.delete("/:id", (req, res) => res.status(204).end());

module.exports = router;
