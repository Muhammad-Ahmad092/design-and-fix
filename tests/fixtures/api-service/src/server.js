const express = require('express');
const { z } = require('zod');
const { User, Order } = require('./models');
const { requireAuth } = require('./auth');

const app = express();
app.use(express.json());
app.use(requireAuth);

const OrderInput = z.object({ sku: z.string(), quantity: z.number().int().positive() });

app.get('/health', (req, res) => res.json({ status: 'ok' }));

// Planted: list route returns every user.
app.get('/users', async (req, res) => {
  const users = await User.findAll();
  res.json(users);
});

app.get('/users/:id', async (req, res) => {
  const user = await User.findByPk(req.params.id);
  if (!user) {
    return res.status(404).json({ error: 'user not found' });
  }
  res.json(user);
});

// Planted: raw body straight into the model.
app.post('/users', async (req, res) => {
  const user = await User.create(req.body);
  res.status(201).json(user);
});

// Paginated and validated: not flagged.
app.get('/orders', async (req, res) => {
  const limit = Math.min(Number(req.query.limit) || 20, 100);
  const offset = Number(req.query.offset) || 0;
  const orders = await Order.findAll({ limit, offset });
  res.json({ items: orders, limit, offset });
});

app.post('/orders', async (req, res) => {
  const parsed = OrderInput.safeParse(req.body);
  if (!parsed.success) {
    return res.status(400).json({ error: 'invalid order' });
  }
  res.status(201).json(await Order.create(parsed.data));
});

// Planted: a second error shape.
app.delete('/orders/:id', async (req, res) => {
  const n = await Order.destroy({ where: { id: req.params.id } });
  if (!n) {
    return res.status(404).json({ message: 'order not found' });
  }
  res.status(204).end();
});

app.listen(process.env.PORT || 3000);
