**free
dcl-s balance packed(9:2);
exec sql select AMOUNT into :balance from CUSTOMER;
if balance > 0;
  balance -= 1;
endif;
