defmodule Shop do
  def total(items) do
    Enum.reduce(items, 0, fn item, acc -> acc + item.price * item.qty end)
  end

  def charge(order) do
    Shop.Repo.insert!(order)
    Shop.Gateway.charge(order.total)
  end
end
