class TeamUsersController < ApplicationController
  before_action :authorize_team_owner!, except: %i[index show accept reject]
  load_and_authorize_resource only: %i[accept reject show]

  def index; end
  def show; end
  def create; end
  def update; end
  def destroy; end
  def accept; end
  def reject; end
end
