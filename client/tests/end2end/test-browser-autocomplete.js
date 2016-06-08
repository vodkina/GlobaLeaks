
describe('forms with autocomplete', function() {

  fit('This browser does not support autocomplete at all', function() {
    var url = '/views/test/autocomplete_on.html';
    browser.get(url);

    var receiptInp = element(by.id('nameInput'));
    var r = '0101011010111010102';
    receiptInp.sendKeys(r);

    element(by.id('submitBtn')).click();

    browser.get(url);

    receiptInp = element(by.id('nameInput'));
    receiptInp.click();
    receiptInp.click();
    browser.pause();
    receiptInp.sendKeys(protractor.Key.ARROW_DOWN);
    receiptInp.sendKeys(protractor.Key.TAB);

    expect(receiptInp.getText()).toEqual(r);
  });

  it('The browser should not autocomplete the Whistleblowers receipt', function() {
    browser.get('/views/test/autocomplete_off.html');

    
  });

});
