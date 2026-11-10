<?php

namespace App\Http\Controllers\Api;

class NoteController extends ApiController
{
    public function __construct()
    {
        $this->middleware('auth.api:optional');
    }

    public function store() {}
}
