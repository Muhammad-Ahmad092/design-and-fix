class PaymentsController < ApplicationController
  before_action :authenticate_user!

  def create
    head :created
  end
end
