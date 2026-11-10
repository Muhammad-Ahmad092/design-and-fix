defmodule Shop.Router do
  use Plug.Router

  plug :match
  plug :dispatch

  get "/orders" do
    send_resp(conn, 200, "[]")
  end

  post "/orders" do
    send_resp(conn, 201, "created")
  end
end
