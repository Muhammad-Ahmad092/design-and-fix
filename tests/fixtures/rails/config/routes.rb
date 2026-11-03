Rails.application.routes.draw do
  # post "/commented" => "orders#purge" is a comment and must not be listed.
  get "up" => "rails/health#show", as: :rails_health_check

  resources :orders
  resources :payments, only: [:create]
end
