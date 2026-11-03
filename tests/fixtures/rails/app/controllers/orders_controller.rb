require "net/http"

class OrdersController < ApplicationController
  def index
    render json: Order.order(:created_at).limit(50)
  end

  def show
    rows = ActiveRecord::Base.connection.exec_query("SELECT * FROM orders WHERE id = #{params[:id]}")
    render json: rows.first
  end

  def create
    reply = Net::HTTP.get_response(URI("https://inventory.internal/reserve"))
    render json: { reserved: reply.code == "200" }, status: :created
  end

  def update
    head :no_content
  end

  def destroy
    head :no_content
  end
end
