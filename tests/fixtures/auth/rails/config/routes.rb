Rails.application.routes.draw do
  resources :nodes

  namespace :admin do
    resources :plugins, constraints: { id: /[#{Plugin::NAME_FORMAT}]*/o } do
      member do
        delete :clean
      end
    end
  end

  resources :teams, only: [] do
    resources :team_users, path: "people" do
      member do
        post :accept
        post :reject
      end
    end
  end
end
