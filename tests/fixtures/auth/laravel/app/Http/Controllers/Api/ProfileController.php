<?php

namespace App\Http\Controllers\Api;

class ProfileController extends ApiController
{
    public function __construct()
    {
        $this->middleware('auth.api')->except('show');
        $this->middleware('auth.api:optional')->only('show');
    }

    public function show($user) {}
    public function follow($user) {}
    public function unFollow($user) {}
}
